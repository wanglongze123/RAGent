"""
Search Agent — 商品搜索与推荐。

流程：
  Step 1: 解析结构化参数（价格区间、品牌排除、属性排除）
          特殊：query 含"都不是"时直接输出精化引导，不做检索
  Step 2: 检索候选（图搜 / 文本检索）
  Step 3: 硬过滤（品牌排除、属性排除）
  Step 4: LLM 裁判 — 从候选中选出真正相关的 product_id（最多3个）
  Step 5: 推商品卡片
  Step 6: 一句话引导 + 商品选择框（用户点击后进入 product_inquiry 查看详情）
"""
import json as _json
import re as _re
from typing import AsyncIterator

from app.agent.middleware import middleware
from app.db import relational as db
from app.db.product_repo import product_repo
from app.models import events as ev
from app.rag.hybrid_retriever import hybrid_retriever


class SearchAgent:

    # ─────────────────────────────────────────────────────────
    # 主入口：分发到各子流程
    # ─────────────────────────────────────────────────────────

    async def run(
        self,
        session_id: str,
        message: str,
        params: dict,
        session: dict,
        image_base64: str | None = None,
    ) -> AsyncIterator[str]:

        order_info = dict(session.get("order_state") or {})

        # ── 模式1：问卷进行中（master 已路由） ──────────────
        if params.get("questionnaire_reply") is not None:
            async for e in self._handle_questionnaire_reply(
                session_id, params["questionnaire_reply"], order_info, session
            ):
                yield e
            return

        query = params.get("query") or message

        # ── 模式2：用户要细化当前结果 ───────────────────────
        if "细化需求" in query:
            last_q = order_info.get("last_search_query", "")
            hint   = _get_category_hint(last_q) if last_q else "如：价格范围、品牌、功能需求"
            yield ev.text_delta(
                f"好的！请告诉我想在哪方面调整，例如：\n· {hint}\n"
                "我会结合之前的搜索条件重新推荐。"
            ).to_sse()
            return

        # ── 模式3：重新搜索 / 都不是 ────────────────────────
        if "重新搜索" in query or "都不是" in query:
            order_info.pop("last_search_query", None)
            order_info.pop("search_questionnaire", None)
            await db.update_order_state(session_id, order_info)
            yield ev.text_delta(
                "好的！请告诉我您的新需求，也可以发一张图片帮我理解。"
            ).to_sse()
            return

        # ── 模式4：模糊文字查询，先走问卷 ─────────────────────
        if params.get("_needs_questionnaire"):
            async for e in self._start_questionnaire(session_id, query, order_info):
                yield e
            return

        # ── 模式5：正常检索（图搜 / 具体文字搜索）────────────
        price_max   = params.get("price_max")
        price_min   = params.get("price_min")
        incl_brands = params.get("include_brands", [])
        excl_brands = params.get("exclude_brands", [])
        excl_attrs  = params.get("exclude_attrs", [])
        async for e in self._do_search(
            session_id, query, price_max, price_min,
            incl_brands, excl_brands, excl_attrs, session, image_base64, order_info,
        ):
            yield e

    # ─────────────────────────────────────────────────────────
    # 问卷：启动
    # ─────────────────────────────────────────────────────────

    async def _start_questionnaire(
        self, session_id: str, original_query: str, order_info: dict
    ) -> AsyncIterator[str]:
        order_info["search_questionnaire"] = {
            "original_query": original_query,
            "step": 1,
            "collected": {},
            "hint": _get_category_hint(original_query),
        }
        await db.update_order_state(session_id, order_info)
        yield ev.clarification(
            question=f"好的，帮您搜索「{original_query}」！先了解一下：您的预算大概是多少？",
            options=["300元以内", "300-600元", "600-1000元", "1000元以上", "不限预算"],
        ).to_sse()

    # ─────────────────────────────────────────────────────────
    # 问卷：处理每一步回复
    # ─────────────────────────────────────────────────────────

    async def _handle_questionnaire_reply(
        self,
        session_id: str,
        message: str,
        order_info: dict,
        session: dict,
    ) -> AsyncIterator[str]:

        questionnaire = order_info.get("search_questionnaire", {})
        step          = questionnaire.get("step", 1)
        collected     = questionnaire.get("collected", {})
        original      = questionnaire.get("original_query", message)
        hint          = questionnaire.get("hint", "如：特定功能、款式偏好")

        # 取消问卷
        if any(kw in message for kw in ("算了", "取消", "不买了", "退出")):
            order_info.pop("search_questionnaire", None)
            await db.update_order_state(session_id, order_info)
            yield ev.text_delta("好的，已退出搜索。有需要随时告诉我！").to_sse()
            return

        # ── Step 1：价格 ────────────────────────────────────
        if step == 1:
            pmin, pmax = _parse_price_option(message)
            if pmin is not None: collected["price_min"] = pmin
            if pmax is not None: collected["price_max"] = pmax
            questionnaire.update({"step": 2, "collected": collected})
            order_info["search_questionnaire"] = questionnaire
            await db.update_order_state(session_id, order_info)
            yield ev.clarification(
                question="品牌有偏好吗？",
                options=["国产品牌", "国际大牌", "不限品牌"],
            ).to_sse()
            return

        # ── Step 2：品牌 ────────────────────────────────────
        if step == 2:
            if "国产" in message:
                collected["brand_pref"] = "国产"
            elif "国际大牌" in message or "大牌" in message:
                collected["brand_pref"] = "international"
            # "不限品牌" → 不记录，保持空
            questionnaire.update({"step": 3, "collected": collected})
            order_info["search_questionnaire"] = questionnaire
            await db.update_order_state(session_id, order_info)
            yield ev.clarification(
                question=f"还有什么特别要求吗？（{hint}）",
                options=["没有，直接搜索"],
            ).to_sse()
            return

        # ── Step 3：品类专属 / 直接搜 ───────────────────────
        if step == 3:
            extra = None if message == "没有，直接搜索" else message
            if extra:
                collected["extra"] = extra

            order_info.pop("search_questionnaire", None)
            await db.update_order_state(session_id, order_info)

            # 合并 query
            combined_query = original
            if extra:
                combined_query = f"{extra}{original}"   # 修饰词前置，如"轻量跑鞋"
            if collected.get("brand_pref") == "国产":
                combined_query = f"国产{combined_query}"

            async for e in self._do_search(
                session_id, combined_query,
                collected.get("price_max"), collected.get("price_min"),
                [], [], [], session, None, order_info,
            ):
                yield e

    # ─────────────────────────────────────────────────────────
    # 核心检索逻辑（原 run() Steps 2-6）
    # ─────────────────────────────────────────────────────────

    async def _do_search(
        self,
        session_id: str,
        query: str,
        price_max,
        price_min,
        incl_brands: list,
        excl_brands: list,
        excl_attrs: list,
        session: dict,
        image_base64: str | None,
        order_info: dict,
    ) -> AsyncIterator[str]:
        try:
            async for e in self._do_search_inner(
                session_id, query, price_max, price_min,
                incl_brands, excl_brands, excl_attrs,
                session, image_base64, order_info,
            ):
                yield e
        except Exception as ex:
            print(f"[search_agent] _do_search 未捕获异常: {ex}")
            yield ev.text_delta("抱歉，搜索时遇到了问题，请稍后重试。").to_sse()

    async def _do_search_inner(
        self,
        session_id: str,
        query: str,
        price_max,
        price_min,
        incl_brands: list,
        excl_brands: list,
        excl_attrs: list,
        session: dict,
        image_base64: str | None,
        order_info: dict,
    ) -> AsyncIterator[str]:

        where   = _build_price_filter(price_max, price_min)
        fetch_k = 10

        if image_base64:
            yield ev.image_searching("正在分析图片…").to_sse()
            try:
                ranked = await hybrid_retriever.retrieve_by_image(
                    image_base64=image_base64, top_k=fetch_k, where=where,
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
                query=query, top_k_chunks=fetch_k * 3, top_k_products=fetch_k, where=where,
            )

        if not ranked:
            yield ev.text_delta("抱歉，暂时没有找到合适的商品，您可以调整一下条件试试。").to_sse()
            return

        if incl_brands:
            ranked = _filter_include_brands(ranked, incl_brands)
        if excl_brands:
            ranked = _filter_brands(ranked, excl_brands)
        if excl_attrs and not image_base64:
            ranked = _filter_attrs(ranked, excl_attrs)

        if not ranked:
            yield ev.text_delta("根据您的筛选条件，暂时没有找到合适的商品。").to_sse()
            return

        if image_base64:
            text_constraint = None if (not query or any(w in query for w in _IMAGE_REF_WORDS)) else query
        else:
            text_constraint = query

        yield ev.tool_progress("llm_judge", "正在筛选最匹配的商品…").to_sse()
        selected_ids = await _llm_judge(text_constraint, ranked, is_image_search=bool(image_base64))

        if selected_ids:
            id_order = {pid: i for i, pid in enumerate(selected_ids)}
            ranked = sorted(
                [r for r in ranked if r["product_id"] in id_order],
                key=lambda r: id_order[r["product_id"]],
            )
        else:
            ranked = ranked[:3]

        shown_products = [p for rp in ranked if (p := product_repo.get(rp["product_id"]))]
        if not shown_products:
            yield ev.text_delta("抱歉，商品信息暂时无法获取。").to_sse()
            return

        # 保存本次搜索上下文，供后续细化时 LLM 合并
        # 图片搜索 query 为空时，用第一款展示商品的类目作为上下文（避免细化时无从合并）
        context_to_save = query if query else (
            shown_products[0].sub_category if shown_products else ""
        )
        if context_to_save:
            order_info["last_search_query"] = context_to_save
            await db.update_order_state(session_id, order_info)

        intro = "根据您上传的图片，为您找到以下相似款：" if image_base64 else "根据您的需求，为您推荐以下商品："
        yield ev.text_delta(intro).to_sse()

        yield ev.product_card_list(
            products=[
                {"product_id": p.product_id, "title": p.display_title,
                 "brand": p.brand, "image_url": p.image_url,
                 "price": p.base_price, "sub_category": p.sub_category}
                for p in shown_products
            ],
            search_type="image" if image_base64 else "text",
        ).to_sse()

        yield ev.clarification(
            question="您可以横向滑动查看所有商品，或：",
            options=["对比这几款", "细化需求", "重新搜索"],
        ).to_sse()


# ─────────────────────────────────────────────────────────
# 问卷辅助函数
# ─────────────────────────────────────────────────────────

_CATEGORY_HINTS: list[tuple[str, str]] = [
    (r"跑鞋|运动鞋|球鞋|跑步",     "如：轻量、防水、缓震"),
    (r"面霜|精华|护肤|乳液|化妆品", "如：敏感肌适用、补水、美白、无酒精"),
    (r"饮料|矿泉水|茶|咖啡|汽水",   "如：无糖、低卡、口味偏好"),
    (r"裤子|上衣|外套|衬衫|T恤|服装", "如：宽松、修身、防风、速干"),
    (r"洗面奶|洁面",               "如：温和清洁、控油、适合油皮/干皮"),
]


def _get_category_hint(query: str) -> str:
    for pattern, hint in _CATEGORY_HINTS:
        if _re.search(pattern, query):
            return hint
    return "如：特定功能、款式偏好、颜色材质"


def _parse_price_option(message: str):
    """把 '300-600元' / '300以内' / '1000以上' 解析成 (price_min, price_max)。"""
    if "不限" in message:
        return None, None
    # "300-600元" or "300~600"
    m = _re.search(r"(\d+(?:\.\d+)?)\s*[-~～到]\s*(\d+(?:\.\d+)?)", message)
    if m:
        return float(m.group(1)), float(m.group(2))
    # "300以内" / "500以下"
    m = _re.search(r"(\d+(?:\.\d+)?)\s*(?:元以内|以内|以下|元以下)", message)
    if m:
        return None, float(m.group(1))
    # "1000以上" / "1000元以上"
    m = _re.search(r"(\d+(?:\.\d+)?)\s*(?:元以上|以上)", message)
    if m:
        return float(m.group(1)), None
    return None, None


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


def _filter_include_brands(ranked: list[dict], incl_brands: list[str]) -> list[dict]:
    """只保留指定品牌的商品（正向品牌过滤，与 _filter_brands 排除方向相反）。"""
    included: set[str] = set()
    for b in incl_brands:
        regional = product_repo.brands_in_region(b)
        if regional:
            included.update(regional)
        else:
            included.add(b)
    return [
        rp for rp in ranked
        if any(inc in rp["metadata"].get("brand", "") for inc in included)
    ]


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
