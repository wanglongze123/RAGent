"""
Master Agent — 导购系统总控层。

职责（只做这三件事，不做业务处理）：
  1. 意图分类：调 LLM 分析用户想干什么，输出结构化 JSON
  2. 状态机路由：根据意图 + 当前状态决定调哪个子 Agent
  3. 上下文管理：维护对话历史、会话状态、最近展示商品

为什么 Master Agent 不直接处理业务？
  职责单一原则。分类、路由、上下文管理是"控制平面"；
  搜索、对比、购物车是"数据平面"。混在一起很快就会变成 God Class。
"""
import json
import re
from typing import AsyncIterator, Optional

from app.agent.middleware import middleware
from app.agent.state_machine import AgentState, get_next_state, is_agent_allowed
from app.db.relational import (
    add_message,
    cart_get,
    create_session,
    get_recent_messages,
    get_session,
    update_session_state,
)
from app.models import events as ev


# 意图 → 子 Agent 名称映射
_INTENT_TO_AGENT: dict[str, str] = {
    "search":           "search",
    "compare":          "compare",
    "scene":            "scene",
    "cart_add":         "cart",
    "cart_manage":      "cart",
    "checkout":         "order",
    "product_inquiry":  "product_inquiry",
    "clarify":          "search",   # 澄清回答归 search 处理
    "chitchat":         "search",   # 闲聊兜底走 search
}


class MasterAgent:

    async def run(
        self,
        session_id: str,
        message: str,
        image_base64: str | None = None,
    ) -> AsyncIterator[str]:
        """
        主入口，yield SSE 事件字符串。
        chat.py 把这里 yield 出来的字符串直接转发给客户端。
        """
        # ── 1. 加载或初始化会话 ──────────────────────────
        session = await get_session(session_id)
        if not session:
            await create_session(session_id)
            session = {
                "agent_state": "browsing",
                "last_shown_products": [],
            }

        current_state = AgentState(session.get("agent_state", "browsing"))

        # ── 2. 保存用户消息到历史 ──────────────────────────
        await add_message(session_id, "user", message or ("[图片]" if image_base64 else ""))

        # ── 3. 推 thinking 事件（用户立刻看到反馈）──────────
        yield ev.thinking("正在理解您的需求...").to_sse()

        history = await get_recent_messages(session_id, limit=10)

        # 图搜捷径：用户传了图，直接走 search 意图，跳过 LLM 意图分类
        # 因为图在文本意图分类里没法表达，LLM 看到的是空 message 或简短文字
        if image_base64:
            intent = "search"
            params = {"query": message or ""}
            needs_clarify = False
            clarify_question = ""
        else:
            # ── 4. 意图分类 ────────────────────────────────────
            intent_result = await self._classify_intent(message, history, session)
            intent = intent_result.get("intent", "search")
            params = intent_result.get("params", {})
            needs_clarify = intent_result.get("clarification_needed", False)
            clarify_question = intent_result.get("clarification_question", "")

        # 后处理：把"第一个"等位置指代解析成真实 product_id
        params = _resolve_position_reference(
            params, session.get("last_shown_products", []), message
        )

        # 后处理：用户原话里的硬过滤关键词（LLM 经常漏填 exclude_brands/exclude_attrs）
        params = _enrich_filters_from_message(params, message)

        # 特殊路由：在 cart_management 状态下的 clarify 意图应路由到 cart
        if intent == "clarify" and current_state == AgentState.CART_MANAGEMENT:
            intent = "cart_add"
            if not params.get("cart_action"):
                params["cart_action"] = "add"

        # 特殊路由：在 checkout 状态下，所有消息都路由到 order agent
        # 用户在填收货信息时说"张三""13800138000"等会被分类成 chitchat/clarify
        # 但这些都是 order agent 需要处理的回答，不能被拒绝。
        # 同时强制 needs_clarify=False，避免被 master 的反问分支拦截。
        if current_state == AgentState.CHECKOUT:
            intent = "checkout"
            needs_clarify = False

        print(f"[master] intent={intent} params={params} last_shown={[p.get('product_id') for p in session.get('last_shown_products', [])][:5]}")

        # ── 5. 需要反问时直接推 clarification 事件 ──────────
        if needs_clarify and clarify_question:
            options = self._build_clarify_options(intent, params)
            yield ev.clarification(clarify_question, options).to_sse()
            yield ev.done(session_id, current_state.value).to_sse()
            return

        # ── 6. 文字搜索：模糊 query 触发问卷（图搜 / 有上下文时直接出结果）──
        if intent == "search" and not image_base64 and not params.get("questionnaire_reply"):
            # 用户已在搜索上下文中（看过结果 / 有历史 query） → 细化，不重新触发问卷
            has_search_context = bool(
                session.get("last_shown_products") or
                (session.get("order_state") or {}).get("last_search_query")
            )
            # scene 主题点击进入的 search：query 来自规划，已足够具体，不触发问卷
            from_scene_topic = bool(params.get("scene_topic"))
            if (not has_search_context
                    and not from_scene_topic
                    and not _is_query_specific_enough(params)):
                params = dict(params)
                params["_needs_questionnaire"] = True

        # ── 7. 状态机：计算下一个状态 ──────────────────────
        next_state = get_next_state(current_state, intent)
        agent_name = _INTENT_TO_AGENT.get(intent, "search")

        # ── 7. 校验子 Agent 是否被允许 ──────────────────────
        # 用 next_state 校验：意图若触发了合法状态转移，
        # 就用新状态判断 agent 权限（如 browsing→cart_management 后 cart agent 可用）
        if not is_agent_allowed(next_state, agent_name):
            yield ev.text_delta(
                "当前阶段暂时无法处理该请求，请先完成当前流程。"
            ).to_sse()
            yield ev.done(session_id, current_state.value).to_sse()
            return

        # ── 8. 路由到子 Agent，收集展示的商品 ──────────────
        old_shown: list[dict] = list(session.get("last_shown_products", []))
        new_shown: list[dict] = []   # 本轮推出的商品（累积所有 product_card 事件）
        assistant_text = []
        collected_blocks: list[dict] = []   # 本轮富内容块（商品卡列表等），随消息存入历史供回填

        # 把最近对话历史注入 session dict，子 Agent 可以直接用
        session["recent_messages"] = history

        # 商品卡是否已即时持久化过（避免客户端中途断开导致 last_shown 丢失）
        last_shown_persisted = False

        # 记录 dispatch 前的购物车数量：
        # 用于判断 order_agent 是否真的提交了订单（提交成功 → 清空购物车）。
        # 仅在路由到 order 时记录，避免无谓的 DB 查询。
        pre_cart_count = 0
        if agent_name == "order":
            pre_cart = await cart_get(session_id)
            pre_cart_count = pre_cart.get("total_count", 0)

        async for event_str in self._dispatch(
            agent_name, session_id, message, params, session, image_base64
        ):
            yield event_str

            # 累积本轮所有 product_card / product_card_list 事件里的商品
            extracted = _extract_products_from_event(event_str)
            if extracted:
                new_shown.extend(extracted)
                # 即时持久化：搜索/对比阶段商品卡推完后客户端可能在推荐理由
                # 流完前断开，update_session_state 不会执行。这里每来新卡立刻写一次，
                # 保证下一轮还能拿到 last_shown。
                ranked_now = [
                    {**p, "rank": i + 1}
                    for i, p in enumerate(new_shown)
                ]
                await update_session_state(
                    session_id,
                    last_shown_products=ranked_now,
                )
                last_shown_persisted = True

            # 收集文本用于存对话历史
            if '"text":' in event_str and "text_delta" in event_str:
                token = _extract_text_delta(event_str)
                if token:
                    assistant_text.append(token)

            # 收集富内容块（商品卡列表等），存入历史供客户端回填还原
            block = _extract_block(event_str)
            if block:
                collected_blocks.append(block)

        # 本轮有新商品则用新的；
        # "重新搜索"/"都不是"由 search_agent 主动清空了 last_shown_products，
        # 不能用 old_shown 覆盖回去，否则清空无效。
        # order_agent 完成下单后也要清空，否则用户下单后搜新商品仍返回旧结果。
        _is_context_reset = (
            agent_name == "search"
            and not new_shown
            and any(kw in message for kw in ("重新搜索", "都不是"))
        )
        # 真正的下单完成判定：
        # order_agent 走多个阶段（购物车确认 / 收货信息 / 提交订单），不能仅凭
        # agent_name == "order" 判断。提交成功后会清空购物车（order_create →
        # cart_clear），取消下单不清空，因此用"购物车数量从非零变零"作为唯一可靠信号。
        post_cart_count = pre_cart_count
        if agent_name == "order" and pre_cart_count > 0:
            post_cart = await cart_get(session_id)
            post_cart_count = post_cart.get("total_count", 0)
        _is_order_complete = (
            agent_name == "order"
            and pre_cart_count > 0
            and post_cart_count == 0
        )
        if new_shown:
            for i, p in enumerate(new_shown):
                p["rank"] = i + 1
            shown_products = new_shown
        elif _is_context_reset or _is_order_complete:
            shown_products = []
        else:
            shown_products = old_shown

        # ── 9. 更新会话状态 ────────────────────────────────
        # next_state == current_state 说明本轮无状态机转移，agent_state 让 sub-agent
        # 自己负责（如 order_agent 提交订单后写 browsing），master 不再覆盖。
        # 否则按 transitions 表把状态推进到 next_state。
        state_to_write = next_state.value if next_state != current_state else None

        if last_shown_persisted:
            # 中途已经持久化过 last_shown，这里只更新可能的状态变化
            if state_to_write is not None:
                await update_session_state(session_id, agent_state=state_to_write)
        else:
            await update_session_state(
                session_id,
                agent_state=state_to_write,
                last_shown_products=shown_products,
            )

        # ── 10. 保存 AI 回复到历史 ─────────────────────────
        # 文本或富块任一非空就存：避免"只出商品卡、无文字"的轮次漏存
        if assistant_text or collected_blocks:
            await add_message(
                session_id,
                "assistant",
                "".join(assistant_text),
                blocks=collected_blocks,
            )

        # ── 10.5 scene 后续引导 ────────────────────────────
        # 下单完成时如果 scene_context 还在，注入主题选择 clarification，
        # 让用户继续浏览场景里其他主题或主动结束购物。
        if _is_order_complete:
            scene_ctx = session.get("scene_context") or {}
            sc_topics = scene_ctx.get("topics") or []
            if sc_topics:
                options = [f"了解{t['theme']}" for t in sc_topics if t.get("theme")]
                options.append("结束购物")
                yield ev.clarification(
                    question="订单已提交！还想看场景里的其他主题吗？",
                    options=options,
                ).to_sse()

        # ── 11. done 事件 ──────────────────────────────────
        yield ev.done(session_id, next_state.value).to_sse()

    # ─────────────────────────────────────────────────────
    # 私有方法
    # ─────────────────────────────────────────────────────

    async def _classify_intent(
        self,
        message: str,
        history: list[dict],
        session: dict,
    ) -> dict:
        """
        意图分类：先走规则快速通道，没命中再 fallback 到 LLM。

        为什么这么做：
          Doubao-Seed-2.0-lite 单次 JSON 模式调用要 5-15s，把它放在每轮请求最前面
          会导致首 Token 延迟严重超标（项目要求 <1s）。电商导购里大半的用户消息
          都是高度模板化的（"加购第二个"/"我要下单"/"对比 A 和 B"/"推荐..."），
          完全可以靠规则秒级判定，把 LLM 留给真正模糊的场景兜底。
        """
        current_state = session.get("agent_state", "browsing")

        order_state = session.get("order_state") or {}

        # 问卷收集阶段：所有消息直接转给 search_agent 作为问卷回复
        if order_state.get("search_questionnaire"):
            return _quick_dict("search", {"questionnaire_reply": message})

        # scene 主题导航："了解X" 且 X 在 scene_context.topics 中 → 走 search 流程
        # 必须放在 product_inquiry / quick_classify 之前，主题名可能含"防晒"等
        # 否则可能被其他关键词截走
        scene_topic = _match_scene_topic(message, session)
        if scene_topic is not None:
            return scene_topic

        # product_inquiry 优先于 _quick_classify，防止"有什么/怎么样"被 search 关键词截走
        if _is_product_inquiry(message, session):
            return _quick_dict("product_inquiry", {"query": message})

        has_pending_sku = bool(order_state.get("pending_sku_product_id"))
        quick = _quick_classify(message, current_state, has_pending_sku)
        if quick is not None:
            return quick

        # cart_management：意图已明确（就是购物车操作），跳过 master LLM
        # cart_agent 自带专项 LLM，context 更小、更快
        if current_state == "cart_management":
            return _quick_dict("cart_manage", {"cart_action": "interpret", "query": message})

        # 走 LLM 慢路径
        context_messages = [
            {"role": m["role"], "content": m["content"]}
            for m in history[-6:]
        ]
        if not context_messages or context_messages[-1]["content"] != message:
            context_messages.append({"role": "user", "content": message})

        last_shown = session.get("last_shown_products", [])
        last_shown_str = json.dumps(last_shown, ensure_ascii=False, indent=2)

        raw = await middleware.chat(
            agent_name="master",
            user_messages=context_messages,
            prompt_vars={"last_shown_products": last_shown_str},
            json_mode=True,
            temperature=0.0,
        )
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return {"intent": "search", "params": {"query": message}}

    async def _dispatch(
        self,
        agent_name: str,
        session_id: str,
        message: str,
        params: dict,
        session: dict,
        image_base64: str | None,
    ) -> AsyncIterator[str]:
        """按 agent_name 调用对应子 Agent"""
        # 延迟导入，避免循环依赖
        from app.agent.sub_agents.search_agent import search_agent
        from app.agent.sub_agents.compare_agent import compare_agent
        from app.agent.sub_agents.scene_agent import scene_agent
        from app.agent.sub_agents.cart_agent import cart_agent
        from app.agent.sub_agents.order_agent import order_agent
        from app.agent.sub_agents.product_inquiry_agent import product_inquiry_agent

        agents = {
            "search":           search_agent,
            "compare":          compare_agent,
            "scene":            scene_agent,
            "cart":             cart_agent,
            "order":            order_agent,
            "product_inquiry":  product_inquiry_agent,
        }
        agent = agents.get(agent_name, search_agent)
        async for event_str in agent.run(
            session_id, message, params, session, image_base64
        ):
            yield event_str

    def _build_clarify_options(self, intent: str, params: dict) -> list[str]:
        """根据意图生成反问选项，引导用户快速选择"""
        if intent == "search" and not params.get("query"):
            return ["美妆护肤", "数码电子", "服饰运动", "食品生活"]
        return []


# ─────────────────────────────────────────────────────────
# 工具函数
# ─────────────────────────────────────────────────────────

def _extract_products_from_event(event_str: str) -> list[dict]:
    """从 product_card / product_card_list / comparison_table SSE 事件中提取商品信息"""
    products = []
    # 这三种事件都带商品列表，要让 last_shown 持久化覆盖到 compare 场景
    if not any(k in event_str for k in ("product_card", "comparison_table")):
        return products
    for line in event_str.strip().split("\n"):
        if not line.startswith("data:"):
            continue
        try:
            data = json.loads(line[5:].strip())
        except Exception:
            continue
        if "product_id" in data:
            products.append({
                "rank": len(products) + 1,
                "product_id": data["product_id"],
                "title": data.get("title", ""),
            })
        elif "products" in data:
            for i, p in enumerate(data["products"]):
                products.append({
                    "rank": i + 1,
                    "product_id": p.get("product_id", ""),
                    "title": p.get("title", ""),
                })
    return products


# 中文位置词 → 排名映射
_POSITION_WORDS: dict[str, int] = {
    "第一": 1, "第一个": 1, "第一款": 1, "第1个": 1, "第1款": 1,
    "第二": 2, "第二个": 2, "第二款": 2, "第2个": 2, "第2款": 2,
    "第三": 3, "第三个": 3, "第三款": 3, "第3个": 3, "第3款": 3,
    "第四": 4, "第四个": 4, "第四款": 4, "第4个": 4, "第4款": 4,
    "第五": 5, "第五个": 5, "第五款": 5, "第5个": 5, "第5款": 5,
}


def _resolve_position_reference(
    params: dict,
    last_shown: list[dict],
    user_message: str = "",
) -> dict:
    """
    把"第一个/第二款/这个"等位置指代解析成真实 product_id。

    Doubao-Seed-2.0-lite 经常把 product_id 输出成 "1"、"第一个"、甚至幻觉成 "P001"
    这种不存在的 id。代码层做三层兜底：
      1. 用户原话里直接含位置词 → 按位置取 last_shown 对应项
      2. LLM 返回的是数字 / 位置词 → 同上
      3. LLM 返回的 product_id 在 last_shown 里查不到 → 改用 last_shown[0]
    """
    pid = params.get("product_id")
    valid_ids = {p.get("product_id") for p in last_shown}

    # 1. 从用户原话直接抓位置词（最可信）
    pos: int | None = None
    for word, p in _POSITION_WORDS.items():
        if word in user_message:
            pos = p
            break

    # 2. LLM 给的 product_id 是数字 / 位置词
    if pos is None and pid and isinstance(pid, str):
        if pid.isdigit():
            pos = int(pid)
        elif pid in _POSITION_WORDS:
            pos = _POSITION_WORDS[pid]

    if pos is not None and 1 <= pos <= len(last_shown):
        params["product_id"] = last_shown[pos - 1].get("product_id", "")
        return params

    # 3. LLM 给了一个 product_id 但它根本不在 last_shown 里 → 当幻觉处理
    if pid and isinstance(pid, str) and pid not in valid_ids and last_shown:
        params["product_id"] = last_shown[0].get("product_id", "")

    return params


# ─────────────────────────────────────────────────────────
# product_inquiry 检测 —— 优先于规则快速通道
# ─────────────────────────────────────────────────────────

# 指代已展示商品的词（"这款"/"那款"/"它"/"第一款"等）
_PRODUCT_REF_WORDS = [
    "这款", "这个", "这件", "这条", "这双", "这瓶", "这盒", "这套",
    "那款", "那个", "那件", "那条", "那双", "那瓶", "那盒", "那套",
    "它", "第一款", "第二款", "第三款", "第一个", "第二个", "第三个",
    "上面的", "刚才的", "前面的", "刚才那款",
]

# 含这些词的消息应走其他意图，不走 product_inquiry
_INQUIRY_EXCLUSIONS = [
    "对比", "比较", "哪个更", "哪个好", "哪款更",         # → compare
    "加购", "加入购物车", "买这个", "买它", "下单",        # → cart
    "推荐", "找一款", "有没有", "求推荐",                  # → search
    "想买", "想要", "我要", "就要", "要买", "我想买",      # → cart_add（购买意图，不是追问）
]


def _is_query_specific_enough(params: dict) -> bool:
    """
    判断搜索 query 是否已有足够结构化约束，可以直接检索。
    规则：有价格 / 品牌过滤 / 3字以上有意义修饰词 → 直接搜索。
    只有 2 字以内的纯品类词（"跑鞋"/"面霜"）→ 触发问卷。
    注：有搜索上下文时（last_shown_products 存在）不会进入此函数。
    """
    if params.get("price_max") or params.get("price_min"):
        return True
    if params.get("exclude_brands") or params.get("include_brands"):
        return True
    query = params.get("query", "")
    filler = {"推荐", "帮我", "找", "买", "看看", "介绍", "好的", "想要", "想买"}
    meaningful = "".join(c for c in query if c not in "".join(filler))
    return len(meaningful) >= 3  # 3字以上（"纯牛奶"/"防水跑鞋"/"蒙牛牌子"等）直接搜


def _match_scene_topic(message: str, session: dict) -> Optional[dict]:
    """
    场景主题导航：识别 "了解X" 模式，X 必须严格等于 scene_context.topics 中某个 theme。

    设计要点：
      - 只匹配以"了解"开头且后续 theme 在场景上下文里的消息，避免误判用户自然语句
        （如"了解一下护肤流程"——theme 不会等于"一下护肤流程"，自然不命中）
      - 匹配成功 → 返回 search 意图，query 取自该 topic 预先规划的 query
      - 复用 search_agent 完整流程（问卷/检索/推卡/加购/对比/下单）
    """
    if not message:
        return None
    msg = message.strip()
    if not msg.startswith("了解"):
        return None
    theme = msg[len("了解"):].strip()
    if not theme:
        return None

    scene_ctx = session.get("scene_context") or {}
    topics = scene_ctx.get("topics") or []
    for t in topics:
        if t.get("theme") == theme:
            query = (t.get("query") or "").strip() or theme
            return _quick_dict("search", {
                "query": query,
                "scene_topic": theme,   # 调试标记：标识本轮 search 来自 scene 导航
            })
    return None


def _is_product_inquiry(message: str, session: dict) -> bool:
    """
    判断是否为对已展示商品的追问：
      1. 当前会话里有已展示商品
      2. 消息含商品指代词
      3. 消息不含对比/加购/新搜索等排除词
    """
    if not session.get("last_shown_products"):
        return False
    if any(k in message for k in _INQUIRY_EXCLUSIONS):
        return False
    return any(w in message for w in _PRODUCT_REF_WORDS)


# ─────────────────────────────────────────────────────────
# 规则快速通道（_quick_classify）—— 让常见意图秒级判定，避开 LLM 的 5-15s 延迟
# ─────────────────────────────────────────────────────────

_CART_ADD_KEYWORDS = ["加购", "加入购物车", "买这个", "买它", "下单这个", "重新加入", "再加"]
_CART_VIEW_KEYWORDS = ["查看购物车", "看看购物车", "我的购物车", "购物车里有"]
_CART_CLEAR_KEYWORDS = ["清空购物车", "全部删除", "都不要了"]
# 删除/修改数量等含参数的操作不走规则，交给 LLM 解析（口语太多样，规则覆盖不全）

_CHECKOUT_KEYWORDS = ["我要下单", "去结账", "结算订单", "提交订单", "我要付款"]
_CHECKOUT_AMBIGUOUS = ["下单", "结账", "购买"]

_COMPARE_KEYWORDS = ["对比", "比较", "哪个更", "哪个好", "哪款更"]

# 场景化组合：明显的多类目编排诉求
_SCENE_KEYWORDS = [
    "度假", "旅游", "出差", "出游", "婚礼", "约会", "面试", "露营", "登山",
    "整套", "搭配方案", "搭一身", "搭一套", "全套", "套装",
    "送礼", "礼物清单", "送给",
    "开学准备", "新生", "宝宝出行",
]

_SEARCH_KEYWORDS = [
    "推荐", "求推荐", "求介绍", "有什么", "找一款", "找一下",
    "想买", "看看有没有", "给我看",
    "细化需求", "重新搜索",   # 系统生成的固定按钮文字，直接快速路由
]

# scene 生命周期控制词（系统按钮文字）：清空场景 / 重新规划全交给 scene_agent 处理
_SCENE_LIFECYCLE_KEYWORDS = ["重新规划", "结束购物"]

_POSITION_RE = re.compile(r"第[一二三四五12345]+(个|款|项|件)?")

# 购买意图词 + 商品指代词组合 → cart_add 快速路由
_PURCHASE_INTENT_WORDS = ["想买", "想要", "要买", "我要", "就要", "我想买"]
_PURCHASE_PRODUCT_REFS = {
    "这款", "那款", "这个", "那个", "这件", "那件", "这条", "那条",
    "第一款", "第二款", "第三款", "第一个", "第二个", "第三个", "它",
}


def _quick_classify(message: str, current_state: str, has_pending_sku: bool = False) -> Optional[dict]:  # noqa: E501
    """
    用纯规则判定意图。返回 LLM JSON 同构 dict，或 None（让 LLM 兜底）。

    电商导购里大半的用户消息高度模板化（"加购第二个"/"我要下单"/"对比 A 和 B"/
    "推荐..."），秒级规则就能搞定。LLM 留给真正模糊的场景。
    """
    msg = message.strip()
    if not msg:
        return None

    if current_state == "checkout":
        return _quick_dict("checkout", {"query": msg})

    # scene 生命周期控制（系统按钮）：交给 scene_agent 自己处理清空与提示
    # 必须放在 cart/checkout 之前，避免"重新规划"被其他规则误判
    if any(k in msg for k in _SCENE_LIFECYCLE_KEYWORDS):
        return _quick_dict("scene", {})

    if any(k in msg for k in _CART_VIEW_KEYWORDS):
        return _quick_dict("cart_manage", {"cart_action": "view"})

    # cart_add 优先于 cart_clear：防止"帮我重新加入购物车"被"清空购物车"截走
    if any(k in msg for k in _CART_ADD_KEYWORDS):
        params: dict = {"cart_action": "add"}
        pos_match = _POSITION_RE.search(msg)
        if pos_match:
            params["product_id"] = pos_match.group()
        return _quick_dict("cart_add", params)

    if any(k in msg for k in _CART_CLEAR_KEYWORDS):
        return _quick_dict("cart_manage", {"cart_action": "clear"})

    # 删除 / 改数量：口语表达多样（"减掉一件"/"只要一个"/"去掉第二个"），
    # 规则无法全覆盖，统一交给 LLM 解析具体参数

    # 购买意图 + 商品指代词 → cart_add（"我想买第一款"/"我要这个"/"就要这款"）
    if (any(k in msg for k in _PURCHASE_INTENT_WORDS) and
            any(w in msg for w in _PURCHASE_PRODUCT_REFS)):
        params: dict = {"cart_action": "add"}
        pos_match = _POSITION_RE.search(msg)
        if pos_match:
            params["product_id"] = pos_match.group()
        return _quick_dict("cart_add", params)

    if any(k in msg for k in _CHECKOUT_KEYWORDS):
        return _quick_dict("checkout", {})

    if any(k == msg or k in msg for k in _CHECKOUT_AMBIGUOUS) and len(msg) <= 6:
        return _quick_dict("checkout", {})

    if any(k in msg for k in _COMPARE_KEYWORDS):
        return _quick_dict("compare", {})

    # 场景化组合优先级要在 search 之前判 — "推荐三亚度假整套" 同时含"推荐"和"度假"，
    # 应判 scene 而不是单品 search
    if any(k in msg for k in _SCENE_KEYWORDS):
        return _quick_dict("scene", {"query": msg})

    if any(k in msg for k in _SEARCH_KEYWORDS):
        # query 直接传原话；下游 hybrid_retriever.parse_query 会抽价格，
        # _enrich_filters_from_message 会抽品牌/属性排除。LLM 的活规则替了
        return _quick_dict("search", {"query": msg})

    # 用户否定了当前搜索结果（"都不是，换个方式找"）→ 快速走 search，
    # search_agent 内部检测"都不是"后直接输出精化引导，不做检索
    if "都不是" in msg:
        return _quick_dict("search", {"query": msg})

    # SKU 规格选择快速通道：仅当上轮已发出规格询问（pending_sku_product_id 存在）时触发。
    # 其他 cart_management 下未识别的消息（改数量、删除等）交给 LLM 精确解析。
    if current_state == "cart_management" and has_pending_sku:
        return _quick_dict("cart_add", {"cart_action": "add"})

    return None


def _quick_dict(intent: str, params: dict) -> dict:
    """统一规则快速通道返回结构（与 LLM JSON 输出对齐）"""
    return {
        "intent": intent,
        "params": params,
        "clarification_needed": False,
        "clarification_question": "",
        "_quick": True,  # 调试标记
    }


# 属性否定关键词（化妆品/食品成分这类硬过滤词，扩商品类目时按需补）
_ATTR_NEGATIONS = [
    ("酒精", ["不含酒精", "无酒精", "不要含酒精", "不要酒精"]),
    ("香精", ["不含香精", "无香精", "不要香精", "无香"]),
    ("防腐剂", ["不含防腐剂", "无防腐剂"]),
    ("色素", ["不含色素", "无色素"]),
    ("油脂", ["不含油脂", "无油脂"]),
]

_BRAND_NEG_PATTERNS = ["不要", "不喜欢", "避开", "排除"]


def _enrich_filters_from_message(params: dict, message: str) -> dict:
    """
    LLM 给的 exclude_brands/exclude_attrs 经常空，从用户原话里再扫一遍补上。

    地域关键词从 product_repo 动态拿 —— 数据里有什么 region 就支持什么，
    新增"韩系/东南亚"这类商品自动生效，不用改代码。
    """
    # 延迟导入，避免循环依赖
    from app.db.product_repo import product_repo

    excl_brands = list(params.get("exclude_brands") or [])
    excl_attrs = list(params.get("exclude_attrs") or [])

    # 地域类型词（含别名）："不要日系" / "避开欧美" / "不要国货"
    region_keywords = product_repo.all_region_keywords()
    for kw in region_keywords:
        if any(f"{neg}{kw}" in message for neg in _BRAND_NEG_PATTERNS):
            if kw not in excl_brands:
                excl_brands.append(kw)

    # 属性否定："不含酒精" / "无香精"
    for attr, patterns in _ATTR_NEGATIONS:
        if any(p in message for p in patterns):
            if attr not in excl_attrs:
                excl_attrs.append(attr)

    if excl_brands:
        params["exclude_brands"] = excl_brands
    if excl_attrs:
        params["exclude_attrs"] = excl_attrs
    return params


def _extract_text_delta(event_str: str) -> str:
    """从 text_delta 事件中提取文本"""
    for line in event_str.strip().split("\n"):
        if not line.startswith("data:"):
            continue
        try:
            data = json.loads(line[5:].strip())
            return data.get("text", "")
        except Exception:
            pass
    return ""


# 入历史的内容型富块类型。clarification/cart_update/tool_progress 等为瞬时交互/状态事件，
# 不入历史（否则回填后会塞满过期的"确认下单""问卷"按钮）。对比结果走 text_delta 文字，
# 由 assistant_text 自动还原，无需入块。comparison_table/product_card 作为兼容项保留。
_BLOCK_EVENT_TYPES = {"product_card_list", "product_card", "comparison_table"}


def _extract_block(event_str: str) -> Optional[dict]:
    """
    从一条 SSE 事件字符串里提取「内容型富块」。
    命中 _BLOCK_EVENT_TYPES 时返回 {"type": 事件类型, "data": 事件data}，否则 None。
    结构与 SSE 事件 data 同构，客户端可复用同一套解析直接重建商品卡。
    """
    etype = None
    data = None
    for line in event_str.strip().split("\n"):
        if line.startswith("event:"):
            etype = line[len("event:"):].strip()
        elif line.startswith("data:"):
            try:
                data = json.loads(line[len("data:"):].strip())
            except Exception:
                data = None
    if etype in _BLOCK_EVENT_TYPES and data is not None:
        return {"type": etype, "data": data}
    return None


master_agent = MasterAgent()
