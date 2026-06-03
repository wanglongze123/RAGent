"""
Search Agent — 商品搜索与推荐（引导式 slot-filling 多轮收敛）。

核心模型（重构后）：
  累积结构化 SearchState（持久化 session.search_state）
    → 每轮把用户输入合并进 SearchState（标量覆盖 / 列表累积 / 反选）
    → 用「完整 SearchState」每轮全量重新检索（不在旧结果里捞 —— 数据仅 100 商品，重检成本可忽略）
    → 「该问才问」：需求欠定时 Agent 顺着该品类的关键维度(slot)清单主动反问，
       每填一个维度候选可见收窄（"还剩 N 款"），直到收敛(≤3 / 无可区分维度 / 用户喊停)才出卡
    → 推荐后仍可继续：加约束重检索 / "换一批" / "不满意"反开调整方向 / 换品类重置

SearchState（存 sessions.search_state）：
  {category, price_min, price_max, include_brands[], exclude_brands[],
   exclude_attrs[], want_attrs[], asked_slots[], shown_ids[], pending{}}
  pending = 上一轮反问的 slot 上下文 {slot, kind, options}，用于把用户的「答复」确定性地
  解释成对应约束（按钮点击发回的就是选项文本），不依赖 master LLM 二次解析；用户若答非所问
  则 pending 失效、按正常输入处理 —— 反问可中断、不锁死用户。
"""
import asyncio
import json as _json
import re as _re
import sys
import traceback
from typing import AsyncIterator, Optional

from app.agent.middleware import middleware
from app.db import relational as db
from app.db.product_repo import product_repo
from app.llm.client import llm_client
from app.models import events as ev
from app.rag.hybrid_retriever import hybrid_retriever


_FETCH_K = 12          # 每轮召回上限（sub_category 最多约 12 个，足够覆盖）
_RECOMMEND_AT = 3      # 候选收敛到 ≤ 此值即直接出卡，不再反问
_SHOW_TOP = 3          # 一次展示的商品数
_MAX_FOLLOW_UPS = 3    # 开放邀请后最多追问次数（不含邀请本身）


class SearchAgent:

    # ─────────────────────────────────────────────────────────
    # 主入口
    # ─────────────────────────────────────────────────────────

    async def run(
        self,
        session_id: str,
        message: str,
        params: dict,
        session: dict,
        image_base64: str | None = None,
    ) -> AsyncIterator[str]:

        state = dict(session.get("search_state") or {})
        msg = (message or "").strip()

        # ── 重置：重新搜索 / 都不是 ───────────────────────────
        if "重新搜索" in msg or "都不是" in msg:
            await db.clear_search_state(session_id)
            await db.update_session_state(session_id, last_shown_products=[])
            yield ev.text_delta(
                "好的！请告诉我您想找什么商品，也可以发一张图片帮我理解。"
            ).to_sse()
            return

        # ── 图搜：一次性视觉检索，并把品类写入 SearchState ──────
        if image_base64:
            async for e in self._do_image(session_id, msg, state, image_base64):
                yield e
            return

        # ── "换一批"：在当前约束的候选里出未展示过的后几名 ─────
        if state.get("category") and any(k in msg for k in _BATCH_WORDS):
            async for e in self._next_batch(session_id, state):
                yield e
            return

        # ── 纯"不满意"（无新信息）：反开"调整方向"反问 ──────────
        if state.get("category") and any(k in msg for k in _DISSATISFY_WORDS):
            yield ev.text_delta("这些不太合适呀，想从哪方面调整一下？").to_sse()
            yield ev.clarification(
                question="选一个方向，我帮您重新找：",
                options=["换一批", "再便宜点", "重新搜索"],
            ).to_sse()
            return

        # ── 放宽约束按钮（0 结果时给出）───────────────────────
        if state.get("category") and msg in ("放宽预算", "不限品牌", "再便宜点"):
            state = _apply_adjustment(state, msg, await self._candidates(state))
            state["pending"] = {}

        else:
            # ── 处理上一轮反问的答复 / 正常合并 ──────────────────
            pending = state.get("pending") or {}
            if pending and any(w in msg for w in _SHOW_NOW_WORDS):
                # 用户喊停 → 默认值逻辑：不再追问，直接用当前约束出卡
                state["pending"] = {}
                await db.update_search_state(session_id, state)
                async for e in self._recommend(session_id, state):
                    yield e
                return
            if pending and _is_slot_answer(msg, pending):
                state = _apply_slot_answer(state, pending, msg)
                state["pending"] = {}
            else:
                # 答非所问 / 首轮 / 主动细化 → pending 失效，走正常 patch 合并
                old_price = (state.get("price_min"), state.get("price_max"))
                state["pending"] = {}
                state = _merge_search_state(state, params, msg)
                # 改主意感知：价格被覆盖时回显
                new_price = (state.get("price_min"), state.get("price_max"))
                if old_price != new_price and any(p is not None for p in old_price):
                    hint = _price_phrase(state)
                    if hint:
                        yield ev.text_delta(f"好的，已将价格调整为{hint}。").to_sse()

        if not state.get("category"):
            # 没有任何可检索的品类线索 → 引导用户给出
            await db.clear_search_state(session_id)
            yield ev.text_delta(
                "想找点什么呢？告诉我商品类型（如「面霜」「跑步鞋」「笔记本」），"
                "我来帮您挑～"
            ).to_sse()
            return

        await db.update_search_state(session_id, state)

        # ── 用户主动喊停（非 pending 场景）→ 直接出卡 ──────────
        if any(w in msg for w in _SHOW_NOW_WORDS):
            async for e in self._recommend(session_id, state):
                yield e
            return

        # ── 首次搜索且信息单薄 → 开放性邀请（不搜索，立即返回）──
        if not state.get("invited") and not _has_preferences(state):
            state["invited"] = True
            await db.update_search_state(session_id, state)
            async for e in self._invite(state):
                yield e
            return

        # ── 全量重检索 → 决策（追问 or 出卡）──────────────────
        async for e in self._search_and_decide(session_id, state):
            yield e

    # ─────────────────────────────────────────────────────────
    # 检索 + 决策：反问下一个维度 or 出卡
    # ─────────────────────────────────────────────────────────

    async def _search_and_decide(
        self, session_id: str, state: dict
    ) -> AsyncIterator[str]:
        yield ev.tool_progress("hybrid_search", "正在为您检索商品…").to_sse()
        try:
            ranked = await self._candidates(state)
        except Exception as ex:
            print(f"[search_agent] 检索异常: {ex}")
            traceback.print_exc(file=sys.stderr)
            yield ev.text_delta("抱歉，搜索时遇到了问题，请稍后重试。").to_sse()
            return

        # 0 结果 → 放宽建议（而非干巴巴"没有"）
        if not ranked:
            async for e in self._relax(state):
                yield e
            return

        cand_products = _cand_products(ranked)
        n = len(cand_products)
        asked_count = len(state.get("asked_slots") or [])

        # 决策：候选够少 / 已追问过 / 已出过卡（用户在精细化筛选）/ 无可区分维度 → 出卡
        slot = None
        if n > _RECOMMEND_AT and asked_count < _MAX_FOLLOW_UPS and not state.get("shown_ids"):
            slot = _next_slot_dynamic(state, cand_products)   # Tier 2: SKU properties
            if slot is None:
                slot = _next_slot_to_ask(state, cand_products)  # Tier 3: 关键词兜底

        if slot is None:
            async for e in self._recommend(session_id, state, ranked=ranked):
                yield e
            return

        # ── 追问（自然语言问题 + 仅逃生按钮）──────────────────
        state.setdefault("asked_slots", []).append(slot["name"])
        state["pending"] = {"slot": slot["name"], "kind": slot["kind"], "options": slot.get("options", [])}
        await db.update_search_state(session_id, state)

        yield ev.text_delta(slot["question"]).to_sse()
        yield ev.clarification(question="", options=["直接帮我搜"]).to_sse()

    # ─────────────────────────────────────────────────────────
    # 出卡（收敛末轮）
    # ─────────────────────────────────────────────────────────

    async def _recommend(
        self,
        session_id: str,
        state: dict,
        ranked: Optional[list] = None,
    ) -> AsyncIterator[str]:
        if ranked is None:
            ranked = await self._candidates(state)
        if not ranked:
            async for e in self._relax(state):
                yield e
            return

        # LLM 裁判从候选里选最匹配的（最多 3 个），失败降级 top-3
        text_constraint = _build_query_from_state(state)
        yield ev.tool_progress("llm_judge", "正在筛选最匹配的商品…").to_sse()
        selected_ids = await _llm_judge(text_constraint, ranked, is_image_search=False)
        if selected_ids:
            order = {pid: i for i, pid in enumerate(selected_ids)}
            ranked = sorted(
                [r for r in ranked if r["product_id"] in order],
                key=lambda r: order[r["product_id"]],
            )
        ranked = ranked[:_SHOW_TOP]

        shown = [p for rp in ranked if (p := product_repo.get(rp["product_id"]))]
        if not shown:
            yield ev.text_delta("抱歉，商品信息暂时无法获取。").to_sse()
            return

        state["shown_ids"] = [p.product_id for p in shown]
        await db.update_search_state(session_id, state)

        echo = _state_label(state)
        yield ev.text_delta(
            f"已按您的需求（{echo}）筛选，为您推荐："
            if echo else "根据您的需求，为您推荐以下商品："
        ).to_sse()

        yield ev.product_card_list(
            products=[
                {"product_id": p.product_id, "title": p.display_title,
                 "brand": p.brand, "image_url": p.image_url,
                 "price": p.base_price, "sub_category": p.sub_category}
                for p in shown
            ],
            search_type="text",
        ).to_sse()

        first_opt = "加入购物车" if len(shown) == 1 else "对比这几款"
        yield ev.clarification(
            question="点击商品卡片可查看详情和选择规格，或：",
            options=[first_opt, "换一批", "重新搜索"],
        ).to_sse()

    # ─────────────────────────────────────────────────────────
    # 换一批：出候选里未展示过的后几名
    # ─────────────────────────────────────────────────────────

    async def _next_batch(self, session_id: str, state: dict) -> AsyncIterator[str]:
        ranked = await self._candidates(state)
        shown_ids = set(state.get("shown_ids") or [])
        rest = [rp for rp in ranked if rp["product_id"] not in shown_ids]
        if not rest:
            yield ev.text_delta("已经把符合条件的商品都展示给您啦。要不要放宽一下条件？").to_sse()
            yield ev.clarification(
                question="选一个方向：",
                options=["放宽预算", "不限品牌", "重新搜索"],
            ).to_sse()
            return

        batch = rest[:_SHOW_TOP]
        shown = [p for rp in batch if (p := product_repo.get(rp["product_id"]))]
        state["shown_ids"] = list(shown_ids) + [p.product_id for p in shown]
        await db.update_search_state(session_id, state)

        yield ev.text_delta("为您换一批其他符合条件的商品：").to_sse()
        yield ev.product_card_list(
            products=[
                {"product_id": p.product_id, "title": p.display_title,
                 "brand": p.brand, "image_url": p.image_url,
                 "price": p.base_price, "sub_category": p.sub_category}
                for p in shown
            ],
            search_type="text",
        ).to_sse()
        first_opt = "加入购物车" if len(shown) == 1 else "对比这几款"
        yield ev.clarification(
            question="点击商品卡片可查看详情，或：",
            options=[first_opt, "换一批", "重新搜索"],
        ).to_sse()

    # ─────────────────────────────────────────────────────────
    # 0 结果 → 放宽建议
    # ─────────────────────────────────────────────────────────

    async def _relax(self, state: dict) -> AsyncIterator[str]:
        opts: list[str] = []
        reason = "当前条件有点严"
        if state.get("price_max") is not None or state.get("price_min") is not None:
            opts.append("放宽预算")
            reason = "可能是预算范围太窄"
        if state.get("include_brands"):
            opts.append("不限品牌")
        opts.append("重新搜索")
        yield ev.text_delta(
            f"没找到完全符合的商品，{reason}。要不要放宽一下条件？"
        ).to_sse()
        yield ev.clarification(question="可以这样调整：", options=opts).to_sse()

    # ─────────────────────────────────────────────────────────
    # 开放性邀请（首次单薄搜索时，不搜索，立即返回）
    # ─────────────────────────────────────────────────────────

    async def _invite(self, state: dict) -> AsyncIterator[str]:
        sub_cat = state.get("category", "商品")
        examples = _invitation_examples(sub_cat)
        yield ev.text_delta(
            f"好的！您对{sub_cat}有什么偏好或要求吗？{examples}"
            "说说您的想法，帮您找得更准～"
        ).to_sse()
        yield ev.clarification(
            question="",
            options=["直接帮我搜"],
        ).to_sse()

    # ─────────────────────────────────────────────────────────
    # 图搜：视觉检索 + 写入 SearchState.category
    # ─────────────────────────────────────────────────────────

    async def _do_image(
        self, session_id: str, query: str, state: dict, image_base64: str
    ) -> AsyncIterator[str]:
        yield ev.image_searching("正在分析图片…").to_sse()
        where = _build_price_filter(state.get("price_max"), state.get("price_min"))

        async def _vlm_preprocess():
            try:
                vlm_result = await llm_client.vlm_chat(
                    prompt=(
                        "请分析这张商品图，返回 JSON，字段：\n"
                        "1. category: 商品所属类目，只能从以下选项选一个："
                        "「美妆护肤」「数码电子」「服饰运动」「食品饮料」，不确定填 null\n"
                        "2. ocr_text: 图中所有可见文字（品牌名、型号、产品名等），没有文字填空字符串\n"
                        "只返回 JSON，不要解释。示例：{\"category\":\"数码电子\",\"ocr_text\":\"iPhone 15 Pro\"}"
                    ),
                    image_base64=image_base64,
                )
                raw = vlm_result.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
                parsed = _json.loads(raw)
                return (parsed.get("category") or None), (parsed.get("ocr_text") or "").strip()
            except Exception as e:
                print(f"[vlm] 预处理失败（降级）: {e}", flush=True, file=sys.stderr)
                return None, ""

        (vlm_category, vlm_ocr), ranked = await asyncio.gather(
            _vlm_preprocess(),
            hybrid_retriever.retrieve_by_image(image_base64=image_base64, top_k=_FETCH_K, where=where),
        )

        _VALID = {"美妆护肤", "数码电子", "服饰运动", "食品饮料"}
        if vlm_category and vlm_category in _VALID:
            filtered = [r for r in ranked if r.get("metadata", {}).get("category") == vlm_category]
            ranked = filtered or ranked
        if vlm_ocr and query:
            query = f"{query} {vlm_ocr}"

        if not ranked:
            yield ev.text_delta("抱歉，没能从图片里识别出匹配的商品，换张图或用文字描述试试？").to_sse()
            return

        text_constraint = None if (not query or any(w in query for w in _IMAGE_REF_WORDS)) else query
        yield ev.tool_progress("llm_judge", "正在筛选最匹配的商品…").to_sse()
        selected_ids = await _llm_judge(text_constraint, ranked, is_image_search=True)
        if selected_ids:
            order = {pid: i for i, pid in enumerate(selected_ids)}
            ranked = sorted([r for r in ranked if r["product_id"] in order], key=lambda r: order[r["product_id"]])
        else:
            ranked = ranked[:2]

        shown = [p for rp in ranked if (p := product_repo.get(rp["product_id"]))]
        if not shown:
            yield ev.text_delta("抱歉，商品信息暂时无法获取。").to_sse()
            return

        score_map = {rp["product_id"]: rp.get("score", 0.0) for rp in ranked}

        # 图搜结果写入 SearchState：品类来自结果（不锁品牌，便于后续"换个牌子"细化）
        new_state = {"category": shown[0].sub_category, "shown_ids": [p.product_id for p in shown]}
        await db.update_search_state(session_id, new_state)

        yield ev.text_delta("根据您上传的图片，为您找到以下相似款：").to_sse()
        yield ev.product_card_list(
            products=[
                {"product_id": p.product_id, "title": p.display_title,
                 "brand": p.brand, "image_url": p.image_url,
                 "price": p.base_price, "sub_category": p.sub_category,
                 "similarity_score": round(score_map.get(p.product_id, 0.0), 4)}
                for p in shown
            ],
            search_type="image",
        ).to_sse()
        first_opt = "加入购物车" if len(shown) == 1 else "对比这几款"
        yield ev.clarification(
            question="点击商品卡片可查看详情，或：",
            options=[first_opt, "换一批", "重新搜索"],
        ).to_sse()

    # ─────────────────────────────────────────────────────────
    # 全量重检索：用完整 SearchState 召回 + 硬过滤（每轮撒网）
    # ─────────────────────────────────────────────────────────

    async def _candidates(self, state: dict) -> list[dict]:
        query = _build_query_from_state(state)
        where = _build_where_filter(state)
        ranked = await hybrid_retriever.retrieve_products(
            query=query, top_k_chunks=_FETCH_K * 3, top_k_products=_FETCH_K, where=where,
        )
        if state.get("include_brands"):
            ranked = _filter_include_brands(ranked, state["include_brands"])
        if state.get("exclude_brands"):
            ranked = _filter_brands(ranked, state["exclude_brands"])
        if state.get("exclude_attrs"):
            ranked = _filter_attrs(ranked, state["exclude_attrs"])
        if state.get("want_attrs"):
            ranked = _filter_want_attrs(ranked, state["want_attrs"])
        return ranked


# ══════════════════════════════════════════════════════════════
# SearchState 合并（确定性规则：标量覆盖 / 列表累积 / 反选 / 话题切换重置）
# ══════════════════════════════════════════════════════════════

# 用户口语 → 数据集真实 sub_category 的别名
_CATEGORY_ALIASES: dict[str, str] = {
    # 服饰运动（只做拼写归一，不做父类→子类的压缩）
    "跑鞋": "跑步鞋", "运动鞋": "跑步鞋", "球鞋": "跑步鞋",
    "登山鞋": "徒步鞋",
    # 数码
    "手机": "智能手机",
    "电脑": "笔记本电脑", "笔记本": "笔记本电脑",
    "耳机": "真无线耳机", "蓝牙耳机": "真无线耳机",
    "平板": "平板电脑",
    # 美妆（只做常见简称）
    "防晒霜": "防晒", "口红": "唇釉", "粉底": "粉底液",
    # 食品
    "零食": "坚果/零食", "坚果": "坚果/零食", "方便面": "方便食品",
}


def _all_subcategories() -> set[str]:
    return {p.sub_category for p in product_repo.all()}


# 否定前缀：若品类词紧跟在这些词之后（窗口内），视为「排除」而非「想要」，不取作品类
_NEGATION_MARKERS = ("不要", "不想要", "不需要", "不是", "不用", "别要", "别", "除了", "不含", "没有", "不喜欢")


def _is_negated(text: str, pos: int) -> bool:
    """品类词出现在 pos，检查其前方小窗口内是否有否定词（如「不要酸奶」）。"""
    window = text[max(0, pos - 4):pos]
    return any(neg in window for neg in _NEGATION_MARKERS)


def _detect_category(text: str) -> Optional[str]:
    """从一段文字里识别真实品类（sub_category），命中别名先归一。
    多个品类词共存时取「最早出现且未被否定」的那个（确定性，
    天然处理「买牛奶不要酸奶」——牛奶在前且酸奶被否定）。"""
    if not text:
        return None

    candidates: list[tuple[int, str]] = []  # (位置, 归一后的品类)
    # 别名
    for alias, canonical in _CATEGORY_ALIASES.items():
        pos = text.find(alias)
        if pos != -1 and not _is_negated(text, pos):
            candidates.append((pos, canonical))
    # 真实 sub_category
    for sc in _all_subcategories():
        if not sc:
            continue
        pos = text.find(sc)
        if pos != -1 and not _is_negated(text, pos):
            candidates.append((pos, sc))

    if not candidates:
        return None
    # 最早出现的优先；同位置时别名（通常更短的口语词）已先入列
    candidates.sort(key=lambda c: c[0])
    return candidates[0][1]


def _merge_search_state(state: dict, params: dict, message: str) -> dict:
    """把 master 解析出的本轮 patch 合并进 SearchState（确定性）。"""
    state = dict(state)
    state.setdefault("want_attrs", [])
    state.setdefault("include_brands", [])
    state.setdefault("exclude_brands", [])
    state.setdefault("exclude_attrs", [])
    state.setdefault("asked_slots", [])

    raw_query = (params.get("query") or "").strip()

    # 话题切换：本轮出现了与当前不同的真实品类 → 重置需求单
    # raw_query 是 master LLM 语义提取的干净词，优先用；为空才降级扫原始 message
    new_cat = _detect_category(raw_query) or (
        _detect_category(message) if not raw_query else None
    )
    if new_cat and new_cat != state.get("category"):
        state = {
            "category": new_cat,
            "price_min": None, "price_max": None,
            "include_brands": [], "exclude_brands": [],
            "exclude_attrs": [], "want_attrs": [],
            "asked_slots": [], "shown_ids": [], "pending": {},
        }
        raw_query = ""  # 品类已吸收，剩余修饰按下方逻辑并入

    # 首次确立品类（同上，raw_query 非空时不扫原始 message）
    if not state.get("category"):
        cat = _detect_category(raw_query) or (
            _detect_category(message) if not raw_query else None
        )
        if cat:
            state["category"] = cat
            raw_query = ""
        elif raw_query:
            # 未命中已知品类，但有查询词 → 剔掉购买意图前缀后作为品类，交给语义检索
            clean = _re.sub(r'^(我想买|我要买|帮我找|帮我买|想买|要买|找一下|找个|买个|买)\s*', '', raw_query).strip()
            state["category"] = clean or raw_query
            raw_query = ""

    # 约束合并前快照，用于检测是否发生了细化（需清空 shown_ids）
    _prev_constraints = (
        state.get("price_min"), state.get("price_max"),
        tuple(state.get("include_brands", [])),
        tuple(state.get("exclude_brands", [])),
        tuple(state.get("exclude_attrs", [])),
        tuple(state.get("want_attrs", [])),
    )

    # 价格：标量覆盖（last-wins，天然处理"不要500了要800"）
    if params.get("price_max") is not None:
        state["price_max"] = params["price_max"]
    if params.get("price_min") is not None:
        state["price_min"] = params["price_min"]

    # 品牌 / 排除属性：并集累积
    state["include_brands"] = _union(state["include_brands"], params.get("include_brands"))
    state["exclude_brands"] = _union(state["exclude_brands"], params.get("exclude_brands"))
    state["exclude_attrs"] = _union(state["exclude_attrs"], params.get("exclude_attrs"))

    # 正向属性：want_attrs 累积
    state["want_attrs"] = _union(state["want_attrs"], params.get("want_attrs"))

    # 防御兜底：master 若把属性误塞进 query（如"轻量跑鞋"），且品类已确立，
    # 则把 query 里去掉品类后的残余词并入 want_attrs（对 master 拆分容错）。
    if raw_query and state.get("category"):
        residue = raw_query.replace(state["category"], "").strip()
        for alias in _CATEGORY_ALIASES:
            residue = residue.replace(alias, "")
        residue = residue.strip(" ，,、。.")
        if residue and not _detect_category(residue):
            state["want_attrs"] = _union(state["want_attrs"], [residue])

    # 同品类细化（加价格/品牌/属性约束）时清空 shown_ids，
    # 否则旧的展示 id 会把新约束下有效的候选全部排除，导致"换一批"无结果。
    _new_constraints = (
        state.get("price_min"), state.get("price_max"),
        tuple(state.get("include_brands", [])),
        tuple(state.get("exclude_brands", [])),
        tuple(state.get("exclude_attrs", [])),
        tuple(state.get("want_attrs", [])),
    )
    if _new_constraints != _prev_constraints:
        state["shown_ids"] = []

    return state


def _union(base: list, extra) -> list:
    out = list(base or [])
    for x in (extra or []):
        if x and x not in out:
            out.append(x)
    return out


def _build_query_from_state(state: dict) -> str:
    parts = [state.get("category") or ""]
    parts += state.get("want_attrs") or []
    return " ".join(p for p in parts if p).strip()


def _state_label(state: dict) -> str:
    """人类可读的约束回显："面霜 · 干皮 · 保湿 · 预算≤200" """
    parts = [state.get("category") or ""]
    parts += state.get("want_attrs") or []
    if state.get("include_brands"):
        parts += state["include_brands"]
    price = _price_phrase(state)
    if price:
        parts.append(f"预算{price}")
    return " · ".join(p for p in parts if p)


def _price_phrase(state: dict) -> str:
    lo, hi = state.get("price_min"), state.get("price_max")
    if lo is not None and hi is not None:
        return f"{int(lo)}-{int(hi)}元"
    if hi is not None:
        return f"≤{int(hi)}元"
    if lo is not None:
        return f"≥{int(lo)}元"
    return ""


# ══════════════════════════════════════════════════════════════
# 引导式反问：品类 → 关键维度(slot) 清单 + 动态选项
# ══════════════════════════════════════════════════════════════

# 维度判别/选项基于商品「标题 + 营销文案」文本（消费维度如肤质/缓震在自由文本里，不在结构化 SKU）。
# slot: {name, question, kind: 'attr'|'budget'|'brand', options: [(label, [keywords...])]（attr 用）}
_CATEGORY_SLOTS: dict[str, list[dict]] = {
    "美妆护肤": [
        {"name": "肤质", "kind": "attr", "question": "您的肤质偏向哪种呢？", "options": [
            ("干皮", ["干皮", "干性", "干燥"]),
            ("油皮", ["油皮", "油性", "控油"]),
            ("敏感肌", ["敏感肌", "敏感", "舒缓", "屏障"]),
        ]},
        {"name": "功效", "kind": "attr", "question": "您更看重哪种功效？", "options": [
            ("保湿补水", ["保湿", "补水", "锁水"]),
            ("美白提亮", ["美白", "提亮", "淡斑"]),
            ("抗老紧致", ["抗老", "紧致", "抗皱", "淡纹"]),
            ("修护屏障", ["修护", "修复", "屏障"]),
        ]},
        {"name": "预算", "kind": "budget", "question": "预算大概在什么范围？", "options": []},
        {"name": "品牌", "kind": "brand", "question": "有没有偏好的品牌？", "options": []},
    ],
    "服饰运动": [
        {"name": "场景", "kind": "attr", "question": "主要在什么场景穿/用呢？", "options": [
            ("跑步", ["跑步", "跑鞋", "慢跑", "公路跑", "马拉松"]),
            ("篮球", ["篮球", "实战", "球场"]),
            ("徒步户外", ["徒步", "登山", "户外", "越野"]),
            ("日常通勤", ["通勤", "日常", "休闲", "百搭"]),
        ]},
        {"name": "偏好", "kind": "attr", "question": "您更看重哪一点？", "options": [
            ("轻量", ["轻量", "轻便", "轻盈", "超轻"]),
            ("缓震", ["缓震", "减震", "回弹", "脚感"]),
            ("防水透气", ["防水", "防泼水", "透气"]),
        ]},
        {"name": "预算", "kind": "budget", "question": "预算大概在什么范围？", "options": []},
        {"name": "品牌", "kind": "brand", "question": "有没有偏好的品牌？", "options": []},
    ],
    "食品饮料": [
        {"name": "偏好", "kind": "attr", "question": "有什么口味/健康偏好吗？", "options": [
            ("无糖低卡", ["无糖", "0糖", "零糖", "低卡", "低脂", "低糖"]),
            ("高蛋白", ["高蛋白", "蛋白", "0脂", "脱脂"]),
            ("整箱囤货", ["整箱", "箱装", "囤货", "多盒", "多瓶"]),
        ]},
        {"name": "预算", "kind": "budget", "question": "预算大概在什么范围？", "options": []},
        {"name": "品牌", "kind": "brand", "question": "有没有偏好的品牌？", "options": []},
    ],
    "数码电子": [
        {"name": "用途", "kind": "attr", "question": "主要用来做什么呢？", "options": [
            ("办公商务", ["办公", "商务", "生产力", "效率"]),
            ("游戏性能", ["游戏", "电竞", "性能", "旗舰"]),
            ("学生学习", ["学生", "学习", "网课", "入门"]),
            ("影音娱乐", ["影音", "追剧", "视频", "大屏"]),
        ]},
        {"name": "偏好", "kind": "attr", "question": "您更看重哪一点？", "options": [
            ("轻薄便携", ["轻薄", "便携", "轻", "纤薄"]),
            ("长续航", ["续航", "电池", "大电池"]),
            ("大屏", ["大屏", "大尺寸", "折叠"]),
        ]},
        {"name": "预算", "kind": "budget", "question": "预算大概在什么范围？", "options": []},
        {"name": "品牌", "kind": "brand", "question": "有没有偏好的品牌？", "options": []},
    ],
}


def _product_text(p) -> str:
    mkt = ""
    if p.rag_knowledge and p.rag_knowledge.marketing_description:
        mkt = p.rag_knowledge.marketing_description
    props = " ".join(v for s in p.skus for v in s.properties.values())
    return f"{p.title} {mkt} {props}"


def _cand_products(ranked: list[dict]) -> list:
    return [p for rp in ranked if (p := product_repo.get(rp["product_id"]))]


def _slots_for(cand_products: list) -> list[dict]:
    """按候选里最常见的一级类目取 slot 清单。"""
    if not cand_products:
        return []
    from collections import Counter
    top_cat = Counter(p.category for p in cand_products).most_common(1)[0][0]
    return _CATEGORY_SLOTS.get(top_cat, [])


def _min_price(p) -> float:
    return min((s.price for s in p.skus), default=p.base_price)


def _budget_options(cand_products: list) -> list[str]:
    """按候选价格分布动态切档（≥2 档才有区分力）。"""
    prices = sorted(_min_price(p) for p in cand_products)
    if len(prices) < 2 or prices[-1] <= prices[0] * 1.2:
        return []
    t1 = prices[len(prices) // 3]
    t2 = prices[(2 * len(prices)) // 3]
    t1, t2 = int(round(t1)), int(round(t2))
    if t2 <= t1:
        t2 = int(round(prices[-1]))
    if t2 <= t1:
        return []
    return [f"{t1}元以内", f"{t1}-{t2}元", f"{t2}元以上"]


def _next_slot_to_ask(state: dict, cand_products: list) -> Optional[dict]:
    """取下一个「未填 + 对当前候选有区分力」的关键维度；都问完/无区分力则 None。"""
    asked = set(state.get("asked_slots") or [])
    want = set(state.get("want_attrs") or [])
    for slot in _slots_for(cand_products):
        if slot["name"] in asked:
            continue
        kind = slot["kind"]
        if kind == "attr":
            # 已经选过该维度任一值 → 视为已填
            labels = {lbl for lbl, _ in slot["options"]}
            if want & labels:
                continue
            present = [
                lbl for lbl, kws in slot["options"]
                if any(_kw_hit(kws, _product_text(p)) for p in cand_products)
            ]
            if len(present) >= 2:
                return {"name": slot["name"], "kind": "attr",
                        "question": slot["question"], "options": present}
        elif kind == "budget":
            if state.get("price_min") is not None or state.get("price_max") is not None:
                continue
            opts = _budget_options(cand_products)
            if len(opts) >= 2:
                return {"name": slot["name"], "kind": "budget",
                        "question": slot["question"], "options": opts}
        elif kind == "brand":
            if state.get("include_brands"):
                continue
            brands = list(dict.fromkeys(p.brand for p in cand_products if p.brand))
            if len(brands) >= 2:
                return {"name": slot["name"], "kind": "brand",
                        "question": slot["question"], "options": brands[:4]}
    return None


def _kw_hit(keywords: list[str], text: str) -> bool:
    return any(kw in text for kw in keywords)


# ══════════════════════════════════════════════════════════════
# 反问答复解析（pending 机制）
# ══════════════════════════════════════════════════════════════

_SHOW_NOW_WORDS = {"先看看", "直接看", "直接看看", "随便", "随便看看", "帮我选",
                   "都行", "无所谓", "看看吧", "不用问了", "直接推荐",
                   "不用了", "直接找", "就这些了", "开始搜索", "开始找", "帮我找",
                   "直接帮我搜", "直接搜"}
_BATCH_WORDS = ["换一批", "换一换", "换一组", "还有别的", "还有其他", "重新推荐"]
_DISSATISFY_WORDS = ["不行", "不满意", "都不行", "不太行", "不够好", "不喜欢这些",
                     "没有合适", "没合适", "没有喜欢", "都不喜欢"]


def _is_slot_answer(msg: str, pending: dict) -> bool:
    """判断本轮是否在回答上一轮反问的 slot（按钮点击=精确命中选项；打字=包含/可解析）。"""
    kind = pending.get("kind")
    options = pending.get("options") or []
    if msg in options or any(o in msg for o in options):
        return True
    if kind == "budget":
        pmin, pmax = _parse_price_option(msg)
        return pmin is not None or pmax is not None
    if kind == "brand":
        return any(b in msg for b in options)
    return False


def _apply_slot_answer(state: dict, pending: dict, msg: str) -> dict:
    state = dict(state)
    kind = pending.get("kind")
    options = pending.get("options") or []
    chosen = next((o for o in options if o in msg or msg in o), msg)
    if kind == "attr":
        state["want_attrs"] = _union(state.get("want_attrs"), [chosen])
    elif kind == "budget":
        pmin, pmax = _parse_price_option(msg)
        if pmin is not None:
            state["price_min"] = pmin
        if pmax is not None:
            state["price_max"] = pmax
    elif kind == "brand":
        state["include_brands"] = _union(state.get("include_brands"), [chosen])
    return state


def _apply_adjustment(state: dict, action: str, ranked: list) -> dict:
    """0结果/不满意时的方向按钮：放宽预算 / 不限品牌 / 再便宜点。"""
    state = dict(state)
    if action == "放宽预算":
        state["price_min"] = None
        state["price_max"] = None
    elif action == "不限品牌":
        state["include_brands"] = []
    elif action == "再便宜点":
        prices = sorted(_min_price(p) for p in _cand_products(ranked)) if ranked else []
        if prices:
            median = prices[len(prices) // 2]
            # 取中位数以下作为新上限（确实收紧）
            state["price_max"] = int(round(median)) if median < (state.get("price_max") or 1e9) else int(round(prices[0]))
    return state


# ══════════════════════════════════════════════════════════════
# 工具函数（沿用）
# ══════════════════════════════════════════════════════════════

def _parse_price_option(message: str):
    """'300-600元' / '300以内' / '1000以上' → (price_min, price_max)。"""
    if "不限" in message:
        return None, None
    m = _re.search(r"(\d+(?:\.\d+)?)\s*[-~～到]\s*(\d+(?:\.\d+)?)", message)
    if m:
        return float(m.group(1)), float(m.group(2))
    m = _re.search(r"(\d+(?:\.\d+)?)\s*(?:元以内|以内|以下|元以下)", message)
    if m:
        return None, float(m.group(1))
    m = _re.search(r"(\d+(?:\.\d+)?)\s*(?:元以上|以上)", message)
    if m:
        return float(m.group(1)), None
    return None, None


_IMAGE_REF_WORDS = {
    "这款", "这个", "这件", "这条", "这双", "这瓶", "这盒", "这套",
    "那款", "那个", "那件", "那条", "那双", "那瓶", "那盒", "那套",
    "这种", "这类", "这样的",
}


async def _llm_judge(
    text_constraint: str | None,
    candidates: list[dict],
    is_image_search: bool = False,
) -> list[str]:
    """从候选里选最符合需求的 product_id（最多3个），失败降级 top-3。"""
    if not candidates:
        return []
    if is_image_search and text_constraint is None:
        return [r["product_id"] for r in candidates[:2]]

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


def _build_price_filter(price_max, price_min) -> dict | None:
    conditions = []
    if price_max is not None:
        conditions.append({"base_price": {"$lte": float(price_max)}})
    if price_min is not None:
        conditions.append({"base_price": {"$gte": float(price_min)}})
    if not conditions:
        return None
    return conditions[0] if len(conditions) == 1 else {"$and": conditions}


# ══════════════════════════════════════════════════════════════
# 类目约束：把 state.category 的任意写法映射到 ChromaDB metadata.category
# ══════════════════════════════════════════════════════════════

# 宽泛词 / 口语词 → 主类目（用于无法通过 sub_category 精确匹配的情况）
_BROAD_TO_MAIN_CAT: dict[str, str] = {
    # 服饰运动
    "上衣": "服饰运动", "衣服": "服饰运动", "服装": "服饰运动", "穿的": "服饰运动",
    "裤子": "服饰运动", "鞋子": "服饰运动", "鞋": "服饰运动",
    "运动服": "服饰运动", "运动装": "服饰运动", "运动": "服饰运动",
    "外套": "服饰运动", "夹克": "服饰运动", "冲锋衣": "服饰运动",
    "睡衣": "服饰运动", "内衣": "服饰运动",
    # 美妆护肤
    "护肤品": "美妆护肤", "化妆品": "美妆护肤", "美妆": "美妆护肤",
    "护肤": "美妆护肤", "彩妆": "美妆护肤", "化妆": "美妆护肤",
    "面部护理": "美妆护肤",
    # 数码电子
    "数码": "数码电子", "电子产品": "数码电子", "电子": "数码电子",
    "3C": "数码电子", "配件": "数码电子",
    # 食品饮料
    "食品": "食品饮料", "吃的": "食品饮料", "零食": "食品饮料",
    "饮料": "食品饮料", "食物": "食品饮料", "吃喝": "食品饮料",
}


def _to_main_cat(category: str) -> str:
    """把 state.category（sub_category / 别名归一词 / 口语宽泛词）统一映射到主类目。
    返回空字符串表示无法确定，不加类目约束。"""
    if not category:
        return ""
    # 1. 精确 sub_category 匹配（最常见路径：面霜→美妆护肤，跑步鞋→服饰运动）
    main = _sub_to_main_cat(category)
    if main:
        return main
    # 2. 宽泛口语词（上衣→服饰运动，护肤品→美妆护肤 …）
    return _BROAD_TO_MAIN_CAT.get(category, "")


def _build_where_filter(state: dict) -> dict | None:
    """生成 ChromaDB WHERE 条件：价格约束 + 类目约束（二者均有时取交集）。
    类目约束在 DB 层就排除错误类目的商品，不依赖后处理。"""
    conditions = []

    if state.get("price_max") is not None:
        conditions.append({"base_price": {"$lte": float(state["price_max"])}})
    if state.get("price_min") is not None:
        conditions.append({"base_price": {"$gte": float(state["price_min"])}})

    cat = state.get("category", "")
    if cat in _all_subcategories():
        # state.category 本身就是精确 sub_category（帽子/面霜/跑步鞋…）→ 细粒度过滤
        conditions.append({"sub_category": cat})
    else:
        # 宽泛词/别名 → 降级到主类目过滤（上衣/护肤品/数码…）
        main_cat = _to_main_cat(cat)
        if main_cat:
            conditions.append({"category": main_cat})

    if not conditions:
        return None
    return conditions[0] if len(conditions) == 1 else {"$and": conditions}


def _filter_include_brands(ranked: list[dict], incl_brands: list[str]) -> list[dict]:
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


def _filter_want_attrs(ranked: list[dict], want_attrs: list[str]) -> list[dict]:
    """正向属性软过滤：保留命中最多 want_attrs 的候选；全 0 命中则不过滤（仅靠召回排序）。"""
    if not want_attrs:
        return ranked
    scored = []
    for rp in ranked:
        p = product_repo.get(rp["product_id"])
        if not p:
            continue
        text = _product_text(p)
        hits = sum(1 for a in want_attrs if a in text)
        scored.append((hits, rp))
    best = max((h for h, _ in scored), default=0)
    if best == 0:
        return ranked  # 文案未字面命中 → 不强过滤，避免误杀
    return [rp for h, rp in scored if h == best]


# ══════════════════════════════════════════════════════════════
# Tier 2：从候选商品 SKU properties 动态提取追问维度
# ══════════════════════════════════════════════════════════════

# 这些是"选定商品后再配置"的维度，不适合在搜索阶段追问
_SKU_SKIP_PROPS = {
    "颜色", "机身颜色", "帽身颜色", "配色", "色号", "色号规格",
    "尺码", "鞋码", "鞋楦",
    "包装", "包装类型", "包装数量", "包装规格",
    "口味", "数量", "每箱数量", "整箱数量", "整箱盒数",
    "整箱规格", "整箱数量", "单盒容量", "单条净含量", "总袋数",
    "内含条数", "净含量", "产品规格",
}

# SKU property key → 自然语言追问模板（{vals} 会被替换为实际候选值）
_SKU_PROP_QUESTIONS: dict[str, str] = {
    "适用性别":     "您是想买男款还是女款？",
    "鞋楦类型":     "您是男生还是女生？",
    "款型":         "您更偏向哪种款型？（候选有：{vals}）",
    "版本":         "您需要哪个版本？（候选有：{vals}）",
    "产品版本":     "您需要哪个版本？（候选有：{vals}）",
    "屏幕尺寸":     "对屏幕尺寸有要求吗？（候选有：{vals}）",
    "存储":         "对存储容量有要求吗？（候选有：{vals}）",
    "存储容量":     "对存储容量有要求吗？（候选有：{vals}）",
    "存储配置":     "对存储容量有要求吗？（候选有：{vals}）",
    "机身存储":     "对存储容量有要求吗？（候选有：{vals}）",
    "固态硬盘容量": "对硬盘容量有要求吗？（候选有：{vals}）",
    "内存容量":     "对内存大小有要求吗？（候选有：{vals}）",
    "运行内存":     "对内存大小有要求吗？（候选有：{vals}）",
    "内存":         "对内存大小有要求吗？（候选有：{vals}）",
    "芯片型号":     "对性能档次有偏好吗？（候选有：{vals}）",
    "芯片":         "对性能档次有偏好吗？（候选有：{vals}）",
    "裤长":         "您想要长裤还是短裤？（候选有：{vals}）",
    "适用人群":     "这是给谁买的？（候选有：{vals}）",
}


def _next_slot_dynamic(state: dict, cand_products: list) -> Optional[dict]:
    """Tier 2：扫描候选商品 SKU properties，找最有区分力的维度（纯代码，零额外LLM调用）。"""
    asked = set(state.get("asked_slots") or [])

    prop_values: dict[str, list[str]] = {}
    for p in cand_products:
        for sku in p.skus:
            for k, v in sku.properties.items():
                if k in _SKU_SKIP_PROPS or k in asked or k not in _SKU_PROP_QUESTIONS:
                    continue
                if k not in prop_values:
                    prop_values[k] = []
                if v not in prop_values[k]:
                    prop_values[k].append(v)

    # 取区分力最强（不同值最多）的维度；同等时优先列表靠前的
    best_key: Optional[str] = None
    best_count = 1  # 至少要有 2 个不同值才有意义
    for k in _SKU_PROP_QUESTIONS:  # 按预设优先级遍历
        if k not in prop_values:
            continue
        vals = prop_values[k]
        if len(vals) > best_count:
            best_count = len(vals)
            best_key = k

    if best_key is None:
        return None

    vals = prop_values[best_key]
    template = _SKU_PROP_QUESTIONS[best_key]
    vals_str = "、".join(vals[:4])
    question = template.replace("{vals}", vals_str) if "{vals}" in template else template

    return {
        "name": best_key,
        "kind": "attr",   # 复用 _apply_slot_answer 的 attr 分支 → 写入 want_attrs
        "question": question,
        "options": vals[:4],
    }


# ══════════════════════════════════════════════════════════════
# 开放性邀请辅助
# ══════════════════════════════════════════════════════════════

def _has_preferences(state: dict) -> bool:
    """用户是否已提供超出品类本身的偏好信息。"""
    return bool(
        state.get("want_attrs") or
        state.get("include_brands") or
        state.get("price_min") is not None or
        state.get("price_max") is not None
    )


def _sub_to_main_cat(sub_cat: str) -> str:
    for p in product_repo.all():
        if p.sub_category == sub_cat:
            return p.category
    return ""


def _invitation_examples(sub_cat: str) -> str:
    hints = {
        "服饰运动": "比如男女款、使用场景、预算……",
        "数码电子": "比如主要用途、预算、品牌偏好……",
        "美妆护肤": "比如肤质、想要的功效、预算……",
        "食品饮料": "比如口味偏好、健康需求、预算……",
    }
    main = _sub_to_main_cat(sub_cat)
    return hints.get(main, "比如预算、品牌偏好、具体用途……")


search_agent = SearchAgent()
