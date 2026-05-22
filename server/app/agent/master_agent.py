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

        # ── 5. 需要反问时直接推 clarification 事件 ──────────
        if needs_clarify and clarify_question:
            options = self._build_clarify_options(intent, params)
            yield ev.clarification(clarify_question, options).to_sse()
            yield ev.done(session_id, current_state.value).to_sse()
            return

        # ── 6. 状态机：计算下一个状态 ──────────────────────
        next_state = get_next_state(current_state, intent)
        agent_name = _INTENT_TO_AGENT.get(intent, "search")

        # ── 7. 校验当前状态是否允许该子 Agent ──────────────
        if not is_agent_allowed(current_state, agent_name):
            yield ev.text_delta(
                "当前阶段暂时无法处理该请求，请先完成当前流程。"
            ).to_sse()
            yield ev.done(session_id, current_state.value).to_sse()
            return

        # ── 8. 路由到子 Agent，收集展示的商品 ──────────────
        shown_products: list[dict] = list(session.get("last_shown_products", []))
        assistant_text = []

        async for event_str in self._dispatch(
            agent_name, session_id, message, params, session, image_base64
        ):
            yield event_str

            # 从 product_card 事件里提取商品 ID，更新 last_shown_products
            new_products = _extract_products_from_event(event_str)
            if new_products:
                shown_products = new_products   # 每轮推荐刷新，不累积

            # 收集文本用于存对话历史
            if '"text":' in event_str and "text_delta" in event_str:
                token = _extract_text_delta(event_str)
                if token:
                    assistant_text.append(token)

        # ── 9. 更新会话状态 ────────────────────────────────
        await update_session_state(
            session_id,
            agent_state=next_state.value,
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
    """从 product_card / product_card_list SSE 事件中提取商品信息"""
    products = []
    if "product_card" not in event_str:
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
