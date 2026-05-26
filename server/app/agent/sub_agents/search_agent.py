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
            order_info.pop("last_search_brands", None)   # 清除图片搜索的品牌缓存
            order_info.pop("search_questionnaire", None)
            await db.update_order_state(session_id, order_info)
            # 同时清空已展示商品，让下次模糊查询重新触发问卷
            await db.update_session_state(session_id, last_shown_products=[])
            yield ev.text_delta(
                "好的！请告诉我您想搜索的新商品，也可以发一张图片帮我理解。"
            ).to_sse()
            return

        # ── 模式4：模糊文字查询，先走问卷 ─────────────────────
        if params.get("_needs_questionnaire"):
            async for e in self._start_questionnaire(session_id, query, order_info):
                yield e
            return

        price_max   = params.get("price_max")
        price_min   = params.get("price_min")
        incl_brands = params.get("include_brands", [])
        excl_brands = params.get("exclude_brands", [])
        excl_attrs  = params.get("exclude_attrs", [])

        # ── 模式5：在已有候选集内细化（有上下文 + 非图搜）────
        # 原则：初次召回是"撒网"，后续细化是"在网里捞"
        # 避免因细化条件重新发散检索导致上下文丢失
        last_shown = session.get("last_shown_products", [])
        if last_shown and not image_base64:
            async for e in self._refine_in_candidates(
                session_id, last_shown, query, price_max, price_min,
                incl_brands, excl_brands, session, order_info,
            ):
                yield e
            return

        # ── 模式6：全量向量检索（首次搜索 / 图搜）────────────
        async for e in self._do_search(
            session_id, query, price_max, price_min,
            incl_brands, excl_brands, excl_attrs, session, image_base64, order_info,
        ):
            yield e

    # ─────────────────────────────────────────────────────────
    # 在已有候选集内细化
    # ─────────────────────────────────────────────────────────

    async def _refine_in_candidates(
        self,
        session_id: str,
        last_shown: list[dict],
        query: str,
        price_max,
        price_min,
        incl_brands: list,
        excl_brands: list,
        session: dict,
        order_info: dict,
    ) -> AsyncIterator[str]:
        """
        在已展示商品中过滤细化，不重新向量检索。
        硬过滤（价格/品牌）→ 语义重排（非结构化属性如尺寸/功能）→ 空则报告没有。
        """
        # 图片搜索的品牌上下文：自动注入保存的品牌
        if not incl_brands:
            saved_brands = order_info.get("last_search_brands", [])
            if saved_brands:
                incl_brands = saved_brands

        shown_ids = [p["product_id"] for p in last_shown]

        # ── Step 1：硬过滤（价格 + 品牌）─────────────────────
        hard_filtered: list = []
        for pid in shown_ids:
            product = product_repo.get(pid)
            if not product:
                continue
            min_price = min((s.price for s in product.skus), default=product.base_price)
            if price_max is not None and min_price > price_max:
                continue
            if price_min is not None and min_price < price_min:
                continue
            if incl_brands and not any(b in product.brand for b in incl_brands):
                continue
            if excl_brands and any(b in product.brand for b in excl_brands):
                continue
            hard_filtered.append(product)

        if not hard_filtered:
            yield ev.text_delta(
                "当前搜索结果中没有符合条件的商品。"
            ).to_sse()
            yield ev.clarification(
                question="您可以：",
                options=["重新搜索"],
            ).to_sse()
            return

        # ── Step 2：属性过滤（从 query 中解析尺寸/容量等数值约束）────────
        # 完全在 Python 层做，通过 title + SKU 属性的字符串匹配实现精确过滤
        effective_query = query or order_info.get("last_search_query", "")
        final_products = _filter_by_numeric_attr(hard_filtered, effective_query)

        # ── Step 3：更新上下文并推送结果 ─────────────────────
        if effective_query:
            order_info["last_search_query"] = effective_query
            await db.update_order_state(session_id, order_info)

        filtered_count = len(final_products)
        original_count = len(shown_ids)
        intro = (
            f"为您筛选出 {filtered_count} 款符合条件的商品："
            if filtered_count < original_count
            else "根据您的需求，为您推荐以下商品："
        )
        yield ev.text_delta(intro).to_sse()

        yield ev.product_card_list(
            products=[
                {"product_id": p.product_id, "title": p.display_title,
                 "brand": p.brand, "image_url": p.image_url,
                 "price": p.base_price, "sub_category": p.sub_category}
                for p in final_products
            ],
            search_type="text",
        ).to_sse()

        first_opt = "加入购物车" if len(final_products) == 1 else "对比这几款"
        yield ev.clarification(
            question="您可以横向滑动查看，或：",
            options=[first_opt, "细化需求", "重新搜索"],
        ).to_sse()

    # ─────────────────────────────────────────────────────────
    # 问卷：启动
    # ─────────────────────────────────────────────────────────

    async def _start_questionnaire(
        self, session_id: str, original_query: str, order_info: dict
    ) -> AsyncIterator[str]:
        order_info["search_questionnaire"] = {
            "original_query": original_query,
        }
        await db.update_order_state(session_id, order_info)
        hint = _get_category_hint(original_query)
        yield ev.clarification(
            question=f"帮您搜索「{original_query}」！有什么特别要求吗？\n（{hint}，也可直接跳过）",
            options=["直接搜索"],
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
        original      = questionnaire.get("original_query", message)

        # 取消问卷
        if any(kw in message for kw in ("算了", "取消", "不买了", "退出")):
            order_info.pop("search_questionnaire", None)
            await db.update_order_state(session_id, order_info)
            yield ev.text_delta("好的，已退出搜索。有需要随时告诉我！").to_sse()
            return

        # 问卷只有一步，收到回复即清除状态
        order_info.pop("search_questionnaire", None)
        await db.update_order_state(session_id, order_info)

        if message == "直接搜索":
            async for e in self._do_search(
                session_id, original, None, None, [], [], [], session, None, order_info,
            ):
                yield e
            return

        # 从自由文本中提取价格约束
        pmin, pmax = _parse_price_option(message)

        # 从自由文本中提取品牌偏好，并拼入 query（硬过滤 incl_brands 留空，用 query 语义召回）
        brand_prefix = ""
        if "国产" in message:
            brand_prefix = "国产"
        elif any(kw in message for kw in ("国际", "大牌", "进口")):
            brand_prefix = "国际大牌"

        # 剩余部分作为额外需求修饰词（去掉价格和品牌关键词后的内容）
        extra = _re.sub(
            r"\d+(?:\.\d+)?\s*(?:元以内|元以下|以内|以下|元以上|以上)"
            r"|\d+(?:\.\d+)?\s*[-~～到]\s*\d+(?:\.\d+)?\s*元?"
            r"|国产品牌?|国际大牌?|大牌|进口品牌?|不限品牌?"
            r"|[，,、。.]+",
            " ", message,
        ).strip()

        combined_query = original
        if extra:
            combined_query = f"{extra} {combined_query}"
        if brand_prefix:
            combined_query = f"{brand_prefix} {combined_query}"

        async for e in self._do_search(
            session_id, combined_query, pmax, pmin, [], [], [], session, None, order_info,
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

        # 文字细化时，若 LLM 没能从图片上下文提取到真实商品名（产生"图片相似款"等占位词），
        # 直接用服务端保存的上下文替换，绕过 LLM 推断：
        #   1. query 回填为 last_search_query（sub_category）
        #   2. include_brands 注入 last_search_brands（图片搜索时的品牌）
        if not image_base64 and (not query or "图片" in query):
            fallback_query = order_info.get("last_search_query", "")
            if fallback_query:
                query = fallback_query
        # 图片搜索的品牌上下文：如果本次没有指定品牌过滤，且上次是图片搜索，自动沿用品牌
        if not image_base64 and not incl_brands:
            saved_brands = order_info.get("last_search_brands", [])
            if saved_brands:
                incl_brands = saved_brands

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

        # 保存本次搜索上下文，供后续细化时复用（绕过 LLM 重推断）
        context_to_save = query if query else (
            shown_products[0].sub_category if shown_products else ""
        )
        if context_to_save:
            order_info["last_search_query"] = context_to_save
        # 图片搜索时额外保存品牌列表（LLM 看不到图片，细化时无法自动恢复品牌）
        if image_base64 and shown_products:
            unique_brands = list(dict.fromkeys(p.brand for p in shown_products))
            order_info["last_search_brands"] = unique_brands
        elif not image_base64:
            # 文字搜索时清除旧的图片品牌缓存，避免错误继承
            order_info.pop("last_search_brands", None)
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

        first_opt = "加入购物车" if len(shown_products) == 1 else "对比这几款"
        yield ev.clarification(
            question="您可以横向滑动查看所有商品，或：",
            options=[first_opt, "细化需求", "重新搜索"],
        ).to_sse()


# ─────────────────────────────────────────────────────────
# 问卷辅助函数
# ─────────────────────────────────────────────────────────

# (正则, 提示文字, 快捷选项列表)
_CATEGORY_HINTS: list[tuple[str, str, list]] = [
    (r"跑鞋|运动鞋|球鞋|跑步",      "如：轻量、防水、缓震",        ["防水", "轻量", "缓震"]),
    (r"面霜|精华|护肤|乳液|化妆品",  "如：补水、美白、适合敏感肌",  ["补水保湿", "美白淡斑", "适合敏感肌"]),
    (r"饮料|矿泉水|茶|咖啡|汽水",    "如：无糖、小瓶、整箱",        ["无糖低卡", "小瓶装", "整箱购买"]),
    (r"裤子|上衣|外套|衬衫|T恤|服装","如：宽松、修身、速干",        ["宽松舒适", "修身显瘦", "速干防风"]),
    (r"洗面奶|洁面",                "如：控油、温和、敏感肌",       ["控油清洁", "温和不刺激", "适合敏感肌"]),
    (r"笔记本|电脑|平板",            "如：轻薄、大屏、长续航",       ["轻薄便携", "大屏", "长续航"]),
    (r"手机",                       "如：拍照、大电池、轻薄",       ["拍照性能好", "大电池", "轻薄"]),
]


def _filter_by_numeric_attr(products: list, query: str) -> list:
    """
    从 query 中解析数值属性约束，在候选集里精确过滤。
    支持：X英寸以上/以下、X克以内、XGB以上 等模式。
    匹配商品 title + SKU 属性值中的数字。
    找不到约束或商品无对应属性时，不过滤（返回原列表）。
    """
    import re as _re2

    # 解析约束：(数值, 单位, 方向)，如 (13, "英寸", "上") 或 (100, "g", "下")
    _UNITS = r"英寸|寸|克|g|G|kg|KG|GB|MB|ml|mL|升|L"
    m = _re2.search(
        rf"(\d+(?:\.\d+)?)\s*({_UNITS})\s*(以上|及以上|以下|以内|以下)",
        query,
    )
    if not m:
        return products  # 没有数值约束，不过滤

    threshold = float(m.group(1))
    unit = m.group(2)
    direction = m.group(3)  # "以上"/"及以上" 或 "以下"/"以内"
    is_min = "上" in direction  # True = 大于等于，False = 小于等于

    filtered = []
    for product in products:
        # 收集商品所有包含该单位的数值
        search_text = product.title + " " + " ".join(
            str(v)
            for sku in product.skus
            for v in sku.properties.values()
        )
        numbers = _re2.findall(rf"(\d+(?:\.\d+)?)\s*{_re2.escape(unit)}", search_text)
        if not numbers:
            filtered.append(product)  # 找不到对应属性，不过滤
            continue
        vals = [float(n) for n in numbers]
        matched = (max(vals) >= threshold) if is_min else (min(vals) <= threshold)
        if matched:
            filtered.append(product)

    return filtered if filtered else products  # 全过滤时兜底返回原列表


def _get_category_hint(query: str) -> str:
    for pattern, hint, _ in _CATEGORY_HINTS:
        if _re.search(pattern, query):
            return hint
    return "如：特定功能、款式偏好、颜色材质"


def _get_category_options(query: str) -> list:
    """返回品类专属快捷需求选项（用于问卷第三步）"""
    for pattern, _, options in _CATEGORY_HINTS:
        if _re.search(pattern, query):
            return options
    return []


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
