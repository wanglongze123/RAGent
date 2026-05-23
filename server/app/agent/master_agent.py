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
from typing import AsyncIterator

from app.agent.middleware import middleware
from app.agent.state_machine import AgentState, get_next_state, is_agent_allowed
from app.db.relational import (
    add_message,
    create_session,
    get_recent_messages,
    get_session,
    update_session_state,
)
from app.models import events as ev


# 意图 → 子 Agent 名称映射
_INTENT_TO_AGENT: dict[str, str] = {
    "search":       "search",
    "compare":      "compare",
    "cart_add":     "cart",
    "cart_manage":  "cart",
    "checkout":     "order",
    "clarify":      "search",   # 澄清回答归 search 处理
    "chitchat":     "search",   # 闲聊兜底走 search
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
        await add_message(session_id, "user", message)

        # ── 3. 推 thinking 事件（用户立刻看到反馈）──────────
        yield ev.thinking("正在理解您的需求...").to_sse()

        # ── 4. 意图分类 ────────────────────────────────────
        history = await get_recent_messages(session_id, limit=10)
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

        # ── 6. 状态机：计算下一个状态 ──────────────────────
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

        # 把最近对话历史注入 session dict，子 Agent 可以直接用
        session["recent_messages"] = history

        # 商品卡是否已即时持久化过（避免客户端中途断开导致 last_shown 丢失）
        last_shown_persisted = False

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

        # 本轮有新商品则用新的，否则保留上一轮的（购物车/下单等不展示商品的操作）
        if new_shown:
            for i, p in enumerate(new_shown):
                p["rank"] = i + 1
            shown_products = new_shown
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
        if assistant_text:
            await add_message(session_id, "assistant", "".join(assistant_text))

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
        """调 LLM 分类意图，返回解析后的字典"""
        # 取最近 6 条历史作为分类上下文（太多浪费 token）
        context_messages = [
            {"role": m["role"], "content": m["content"]}
            for m in history[-6:]
        ]
        # 当前消息已在 history 末尾，不重复添加
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
            # 解析失败时兜底：按 search 处理
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
        from app.agent.sub_agents.cart_agent import cart_agent
        from app.agent.sub_agents.order_agent import order_agent

        agents = {
            "search":  search_agent,
            "compare": compare_agent,
            "cart":    cart_agent,
            "order":   order_agent,
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


# 关键词兜底：LLM 经常漏识别"不要日系""不含酒精"这类硬过滤
_BRAND_KEYWORDS = ["日系", "欧美", "国产", "国货"]
_ATTR_NEGATIONS = [
    ("酒精", ["不含酒精", "无酒精", "不要含酒精", "不要酒精"]),
    ("香精", ["不含香精", "无香精", "不要香精", "无香"]),
    ("防腐剂", ["不含防腐剂", "无防腐剂"]),
    ("色素", ["不含色素", "无色素"]),
    ("油脂", ["不含油脂", "无油脂"]),
]


def _enrich_filters_from_message(params: dict, message: str) -> dict:
    """LLM 给的 exclude_brands/exclude_attrs 经常空，从用户原话里再扫一遍补上"""
    excl_brands = list(params.get("exclude_brands") or [])
    excl_attrs = list(params.get("exclude_attrs") or [])

    # 品牌类型词："不要日系" / "不要欧美的" / "不要国货"
    for kw in _BRAND_KEYWORDS:
        if f"不要{kw}" in message or f"不喜欢{kw}" in message or f"避开{kw}" in message:
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


master_agent = MasterAgent()
