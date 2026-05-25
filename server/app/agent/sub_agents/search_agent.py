"""
Search Agent — 商品搜索与推荐。

流程：
  Step 1: 解析结构化参数（价格区间、品牌排除、属性排除）
  Step 2: 检索候选（图搜 / 文本检索）
  Step 3: 硬过滤（品牌排除、属性排除）
  Step 4: LLM 裁判 — 从候选中选出真正相关的 product_id（最多3个）
  Step 5: 推商品卡片（只推裁判选中的）
  Step 6: 流式生成推荐理由（与卡片完全同步）
"""
import json as _json
from typing import AsyncIterator

from app.agent.middleware import middleware
from app.db.product_repo import product_repo
from app.models import events as ev
from app.rag.hybrid_retriever import hybrid_retriever


class SearchAgent:

    async def run(
        self,
        session_id: str,
        message: str,
        params: dict,
        session: dict,
        image_base64: str | None = None,
    ) -> AsyncIterator[str]:

        # ── Step 1: 解析结构化参数 ──────────────────────────
        query       = params.get("query") or message
        price_max   = params.get("price_max")
        price_min   = params.get("price_min")
        excl_brands = params.get("exclude_brands", [])
        excl_attrs  = params.get("exclude_attrs", [])

        where   = _build_price_filter(price_max, price_min)
        fetch_k = 10   # 统一多取候选，交给 LLM 裁判精选

        # ── Step 2: 检索候选 ────────────────────────────────
        if image_base64:
            yield ev.image_searching("正在分析图片…").to_sse()
            try:
                ranked = await hybrid_retriever.retrieve_by_image(
                    image_base64=image_base64,
                    top_k=fetch_k,
                    where=where,
                )
            except Exception as e:
                yield ev.text_delta(f"图片识别失败：{e}").to_sse()
                return
            if not ranked:
                yield ev.text_delta(
                    "图片索引为空或没找到匹配商品，请确认服务端跑过 build_index --with-images。"
                ).to_sse()
                return
        else:
            yield ev.tool_progress("hybrid_search", "正在为您检索相关商品...").to_sse()
            ranked = await hybrid_retriever.retrieve_products(
                query=query,
                top_k_chunks=fetch_k * 3,
                top_k_products=fetch_k,
                where=where,
            )

        if not ranked:
            yield ev.text_delta("抱歉，暂时没有找到合适的商品，您可以调整一下条件试试。").to_sse()
            return

        # ── Step 3: 硬过滤（品牌 / 属性排除）──────────────
        if excl_brands:
            ranked = _filter_brands(ranked, excl_brands)
        if excl_attrs and not image_base64:   # 图搜没有 chunk 文本，跳过属性过滤
            ranked = _filter_attrs(ranked, excl_attrs)

        if not ranked:
            yield ev.text_delta("根据您的筛选条件，暂时没有找到合适的商品。").to_sse()
            return

        # ── Step 4: LLM 裁判 ────────────────────────────────
        # 图搜候选已按视觉相似度降序排列，裁判必须知道这个排名含义才能正确取舍。
        # 四种情况：
        #   纯文字         → text_constraint=query，裁判按文字相关性判
        #   纯图片         → text_constraint=None，裁判按视觉排名取前排
        #   图片+指代词     → "这款/这个"指向图片本身，不提供新筛选信息
        #                    → text_constraint=None，等同纯图片
        #   图片+明确文字   → "只要农夫山泉"等具体约束 → text_constraint=query
        if image_base64:
            if not query or any(w in query for w in _IMAGE_REF_WORDS):
                text_constraint = None        # 无实质文字约束
            else:
                text_constraint = query       # 有品牌/商品名等具体约束
        else:
            text_constraint = query

        yield ev.tool_progress("llm_judge", "正在筛选最匹配的商品…").to_sse()
        selected_ids = await _llm_judge(
            text_constraint, ranked, is_image_search=bool(image_base64)
        )

        # 按裁判顺序重排；若裁判返回空则降级取前3
        if selected_ids:
            id_order = {pid: i for i, pid in enumerate(selected_ids)}
            ranked = sorted(
                [r for r in ranked if r["product_id"] in id_order],
                key=lambda r: id_order[r["product_id"]],
            )
        else:
            ranked = ranked[:3]

        # ── Step 5: 推商品卡片（与 Step 6 的 context 完全同步）──
        context_parts: list[str] = []
        for rp in ranked:
            product = product_repo.get(rp["product_id"])
            if not product:
                continue

            yield ev.product_card(
                product_id=product.product_id,
                title=product.title,
                brand=product.brand,
                image_url=product.image_url,
                price=product.base_price,
                sub_category=product.sub_category,
            ).to_sse()

            # 文本资料：文本检索有 hit_chunks；图搜用 marketing_description
            if rp.get("hit_chunks"):
                chunk_texts = "\n".join(c.content for c in rp["hit_chunks"][:3])
            else:
                mk = (product.rag_knowledge.marketing_description or "") if product.rag_knowledge else ""
                chunk_texts = mk[:600]

            context_parts.append(
                f"【{product.title}】\n品牌: {product.brand} | 类目: {product.sub_category}\n{chunk_texts}"
            )

        if not context_parts:
            yield ev.text_delta("抱歉，商品信息暂时无法获取。").to_sse()
            return

        # ── Step 6: 流式生成推荐理由 ────────────────────────
        context = "\n\n---\n\n".join(context_parts)

        history = session.get("recent_messages", [])

        def _clean_content(role: str, content: str) -> str:
            if role == "user" and content == "[图片]":
                return "（图片搜索）"
            return content

        user_messages = [
            {"role": m["role"], "content": _clean_content(m["role"], m["content"])}
            for m in history[-4:]
        ]

        if image_base64 and not message.strip():
            effective_message = "根据图片为我推荐相似款商品"
        elif image_base64:
            effective_message = f"根据图片推荐相似款，我的额外要求：{message}"
        else:
            effective_message = message

        if not user_messages or user_messages[-1]["content"] != effective_message:
            user_messages.append({"role": "user", "content": effective_message})

        async for token in middleware.chat_stream(
            agent_name="search",
            user_messages=user_messages,
            prompt_vars={"context": context},
            temperature=0.7,
        ):
            yield ev.text_delta(token).to_sse()


# 图片指代词：用户文字只是指向图片，不携带额外筛选信息
_IMAGE_REF_WORDS = {
    "这款", "这个", "这件", "这条", "这双", "这瓶", "这盒", "这套",
    "那款", "那个", "那件", "那条", "那双", "那瓶", "那盒", "那套",
    "这种", "这类", "这样的",
}


# ─────────────────────────────────────────────────────────
# LLM 裁判
# ─────────────────────────────────────────────────────────

async def _llm_judge(
    text_constraint: str | None,
    candidates: list[dict],
    is_image_search: bool = False,
) -> list[str]:
    """
    从候选商品中选出最符合需求的 product_id 列表（最多3个）。
    失败时降级返回前3个。

    is_image_search=True 且 text_constraint=None 时直接取视觉 top-2，
    不调 LLM——裁判看不到图片，让它判视觉排名毫无意义且慢。
    """
    if not candidates:
        return []

    # 纯图片 / 图片+指代词：视觉检索已排好序，跳过裁判直接取前2
    if is_image_search and text_constraint is None:
        return [r["product_id"] for r in candidates[:2]]

    # 加入序号，让裁判感知排名
    lines = [
        f"- [#{i + 1}] id={r['product_id']} 《{r['metadata'].get('title', '')}》 "
        f"品牌:{r['metadata'].get('brand', '')} 类目:{r['metadata'].get('sub_category', '')}"
        for i, r in enumerate(candidates)
    ]

    if is_image_search:
        header = "候选商品（按视觉相似度排序，#1 最匹配图片中的商品，请优先选排名靠前的）："
        if text_constraint:
            header += f"\n用户额外指定：{text_constraint}（可进一步筛选品牌/类型，但不能忽视视觉排名）"
        content = header + "\n" + "\n".join(lines)
    else:
        content = f"用户需求：{text_constraint}\n\n候选商品：\n" + "\n".join(lines)

    try:
        raw = await middleware.chat(
            agent_name="search_judge",
            user_messages=[{"role": "user", "content": content}],
            json_mode=True,
            temperature=0.0,
        )
        result = _json.loads(raw)
        selected = result.get("selected_ids", [])
        valid = {r["product_id"] for r in candidates}
        filtered = [pid for pid in selected if pid in valid]
        return filtered if filtered else [r["product_id"] for r in candidates[:3]]
    except Exception as e:
        print(f"[search_judge] 调用失败，降级 top-3: {e}")
        return [r["product_id"] for r in candidates[:3]]


# ─────────────────────────────────────────────────────────
# 工具函数
# ─────────────────────────────────────────────────────────

def _build_price_filter(price_max, price_min) -> dict | None:
    conditions = []
    if price_max is not None:
        conditions.append({"base_price": {"$lte": float(price_max)}})
    if price_min is not None:
        conditions.append({"base_price": {"$gte": float(price_min)}})
    if not conditions:
        return None
    return conditions[0] if len(conditions) == 1 else {"$and": conditions}


def _filter_brands(ranked: list[dict], excl_brands: list[str]) -> list[dict]:
    excluded: set[str] = set()
    for b in excl_brands:
        regional = product_repo.brands_in_region(b)
        if regional:
            excluded.update(regional)
        else:
            excluded.add(b)
    return [
        rp for rp in ranked
        if not any(ex in rp["metadata"].get("brand", "") for ex in excluded)
    ]


def _filter_attrs(ranked: list[dict], excl_attrs: list[str]) -> list[dict]:
    result = []
    for rp in ranked:
        chunk_contents = " ".join(c.content for c in rp.get("hit_chunks", []))
        if not any(attr in chunk_contents for attr in excl_attrs):
            result.append(rp)
    return result


search_agent = SearchAgent()
