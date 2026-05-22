"""
Search Agent — 商品搜索与推荐。

Skill 流程（固定步骤，不走 ReAct）：
  Step 1: 解析结构化参数（价格区间、品牌排除、属性排除）
  Step 2: 推 tool_progress 事件
  Step 3: 调 hybrid_retriever 检索（结构化过滤 + 语义检索）
  Step 4: 后处理过滤（品牌排除、属性排除）
  Step 5: 推 product_card 事件（数据从 product_repo 取，不经模型）
  Step 6: 流式生成推荐理由（经 middleware，带 RAG 上下文）
"""
from typing import AsyncIterator

from app.agent.middleware import middleware
from app.db.product_repo import product_repo
from app.models import events as ev
from app.rag.hybrid_retriever import hybrid_retriever


# 品牌类型关键词 → 实际品牌名映射
# 用户说"不要日系"，需要映射到数据集里真实的品牌名才能过滤
_BRAND_CATEGORY_MAP: dict[str, list[str]] = {
    "日系": ["资生堂", "珊珂", "芳珂", "安热沙"],
    "欧美": ["雅诗兰黛", "兰蔻", "巴黎欧莱雅", "理肤泉"],
    "国产": ["珀莱雅", "花西子", "完美日记", "薇诺娜"],
    "国货": ["珀莱雅", "花西子", "完美日记", "薇诺娜"],
}


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
        query        = params.get("query") or message
        price_max    = params.get("price_max")
        price_min    = params.get("price_min")
        excl_brands  = params.get("exclude_brands", [])
        excl_attrs   = params.get("exclude_attrs", [])

        # 价格区间 → Chroma metadata filter
        where = _build_price_filter(price_max, price_min)

        # ── Step 2: 告知用户正在检索 ────────────────────────
        yield ev.tool_progress("hybrid_search", "正在为您检索相关商品...").to_sse()

        # ── Step 3: 混合检索 ─────────────────────────────────
        # 有排除条件时多取一些候选，过滤后保证够 5 个
        top_k = 5
        fetch_k = top_k * 2 if (excl_brands or excl_attrs) else top_k

        ranked = await hybrid_retriever.retrieve_products(
            query=query,
            top_k_chunks=fetch_k * 3,
            top_k_products=fetch_k,
            where=where,
        )

        # ── Step 4: 后处理过滤（品牌排除 + 属性排除）────────
        if excl_brands:
            ranked = _filter_brands(ranked, excl_brands)
        if excl_attrs:
            ranked = _filter_attrs(ranked, excl_attrs)
        ranked = ranked[:top_k]

        if not ranked:
            yield ev.text_delta("抱歉，根据您的条件暂时没有找到合适的商品，您可以调整一下筛选条件试试。").to_sse()
            return

        # ── Step 5: 推商品卡片 ───────────────────────────────
        # 关键：卡片字段全从 product_repo 取，不经大模型，杜绝价格/标题幻觉
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

            # 给 LLM 的资料：标题 + 最相关的 3 个 chunk
            chunk_texts = "\n".join(c.content for c in rp["hit_chunks"][:3])
            context_parts.append(
                f"【{product.title}】\n品牌: {product.brand} | 类目: {product.sub_category}\n{chunk_texts}"
            )

        # ── Step 6: 流式生成推荐理由 ─────────────────────────
        context = "\n\n---\n\n".join(context_parts)

        # 带入最近几轮对话历史，让推荐理由能呼应上文
        history = session.get("_recent_messages", [])
        user_messages = [
            {"role": m["role"], "content": m["content"]}
            for m in history[-4:]
        ]
        if not user_messages or user_messages[-1]["content"] != message:
            user_messages.append({"role": "user", "content": message})

        async for token in middleware.chat_stream(
            agent_name="search",
            user_messages=user_messages,
            prompt_vars={"context": context},
            temperature=0.7,
        ):
            yield ev.text_delta(token).to_sse()


# ─────────────────────────────────────────────────────────
# 工具函数
# ─────────────────────────────────────────────────────────

def _build_price_filter(price_max, price_min) -> dict | None:
    """价格区间 → Chroma where 条件"""
    conditions = []
    if price_max is not None:
        conditions.append({"base_price": {"$lte": float(price_max)}})
    if price_min is not None:
        conditions.append({"base_price": {"$gte": float(price_min)}})
    if not conditions:
        return None
    return conditions[0] if len(conditions) == 1 else {"$and": conditions}


def _filter_brands(ranked: list[dict], excl_brands: list[str]) -> list[dict]:
    """
    品牌排除过滤。
    用户可能说"不要日系"（类型词）或"不要资生堂"（具体品牌名）。
    先把类型词展开成具体品牌名，再做精确匹配。
    这一步是硬过滤：代码保证 100% 生效，不依赖模型判断。
    """
    excluded: set[str] = set()
    for b in excl_brands:
        if b in _BRAND_CATEGORY_MAP:
            excluded.update(_BRAND_CATEGORY_MAP[b])
        else:
            excluded.add(b)

    return [
        rp for rp in ranked
        if not any(ex in rp["metadata"].get("brand", "") for ex in excluded)
    ]


def _filter_attrs(ranked: list[dict], excl_attrs: list[str]) -> list[dict]:
    """
    属性排除过滤。
    检查商品的 chunk 内容里是否明确提到排除属性。
    如"不含酒精"→ 过滤掉 chunk 里提到"酒精"的商品。
    """
    result = []
    for rp in ranked:
        chunk_contents = " ".join(c.content for c in rp.get("hit_chunks", []))
        if not any(attr in chunk_contents for attr in excl_attrs):
            result.append(rp)
    return result


search_agent = SearchAgent()
