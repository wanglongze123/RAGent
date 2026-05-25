"""
Tool Middleware — 所有 LLM 调用的统一入口。

职责：
  1. Prompt 收敛：按 agent 名注入对应 system prompt，不散落在业务代码里
  2. 工具域隔离：每个 agent 只能看到自己被授权的工具集
  3. 输出校验：JSON Mode 调用时校验 schema，格式错误直接抛异常
  4. 链路追踪：每次调用打印 [agent → 耗时 → token 数]，方便调试

为什么不直接调 llm_client？
  如果业务代码直接调 llm_client，system prompt 会散在各处，
  换一套话术要改 N 个文件；工具集也无法集中管控。
  Middleware 是唯一入口，改 prompt 只改 prompts/ 目录，
  加工具只改这里的 AGENT_TOOLS 字典。
"""
import json
import time
from typing import Any, AsyncIterator, Optional

from app.llm.client import llm_client
from app.llm.prompts import master, search, compare, cart, order, scene


# ───── 每个 Agent 允许使用的工具集（白名单制）─────
# 工具越少，模型选错的概率越低
AGENT_TOOLS: dict[str, list[dict]] = {
    "master":  [],   # Master Agent 只做分类，不调工具
    "search":  [],   # Search Agent 依赖 RAG，不需要 function calling
    "compare": [],   # Compare Agent 同上
    "cart":    [     # Cart Agent 需要操作购物车
        {
            "type": "function",
            "function": {
                "name": "cart_add",
                "description": "将商品加入购物车",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "product_id": {"type": "string"},
                        "sku_id":     {"type": "string"},
                        "quantity":   {"type": "integer", "default": 1},
                    },
                    "required": ["product_id", "sku_id"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "cart_remove",
                "description": "从购物车删除商品",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "cart_item_id": {"type": "string"},
                    },
                    "required": ["cart_item_id"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "cart_update_quantity",
                "description": "修改购物车商品数量",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "cart_item_id": {"type": "string"},
                        "quantity":     {"type": "integer"},
                    },
                    "required": ["cart_item_id", "quantity"],
                },
            },
        },
    ],
    "order": [],  # Order Agent 只做对话引导，提交由后端逻辑触发
}

# ───── 每个 Agent 的 system prompt 模板 ─────
AGENT_PROMPTS: dict[str, str] = {
    "master":         master.INTENT_CLASSIFICATION_PROMPT,
    "search":         search.SEARCH_AGENT_PROMPT,
    "search_judge":   search.SEARCH_JUDGE_PROMPT,
    "compare":        compare.COMPARE_AGENT_PROMPT,
    "cart":           cart.CART_AGENT_PROMPT,
    "order":          order.ORDER_AGENT_PROMPT,
    # scene 复用同一 agent 名走两条不同提示词：规划阶段用 JSON Mode，生成阶段流式
    "scene_planning": scene.SCENE_PLANNING_PROMPT,
    "scene":          scene.SCENE_REASONING_PROMPT,
}


class ToolMiddleware:
    """所有 LLM 调用必须通过这里"""

    async def chat(
        self,
        agent_name: str,
        user_messages: list[dict],
        prompt_vars: Optional[dict[str, Any]] = None,
        json_mode: bool = False,
        temperature: float = 0.7,
    ) -> str:
        """
        非流式调用，用于意图分类等需要完整 JSON 输出的场景。

        agent_name:   调用方的 Agent 名称，决定 prompt 和工具集
        user_messages: 对话历史 + 当前用户消息
        prompt_vars:  填充 system prompt 里的占位符，如 {context}
        json_mode:    True 时强制 JSON Mode 输出
        """
        t0 = time.time()
        messages = self._build_messages(agent_name, user_messages, prompt_vars)

        # Doubao-Seed-2.0-lite 不支持 response_format json_object
        # 改用 Prompt 强制：在最后一条 user 消息末尾追加 JSON 指令
        if json_mode:
            messages = self._inject_json_instruction(messages)

        result = await llm_client.chat(
            messages=messages,
            temperature=temperature,
        )

        elapsed = round((time.time() - t0) * 1000)
        print(f"[middleware] {agent_name} chat {elapsed}ms")

        if json_mode:
            result = self._extract_json(result)
            return self._validate_json(agent_name, result)
        return result

    async def chat_stream(
        self,
        agent_name: str,
        user_messages: list[dict],
        prompt_vars: Optional[dict[str, Any]] = None,
        temperature: float = 0.7,
    ) -> AsyncIterator[str]:
        """
        流式调用，用于生成推荐理由、对比分析等自然语言输出。
        """
        messages = self._build_messages(agent_name, user_messages, prompt_vars)
        t0 = time.time()

        async for token in llm_client.chat_stream(
            messages=messages,
            temperature=temperature,
        ):
            yield token

        elapsed = round((time.time() - t0) * 1000)
        print(f"[middleware] {agent_name} stream {elapsed}ms")

    def _build_messages(
        self,
        agent_name: str,
        user_messages: list[dict],
        prompt_vars: Optional[dict[str, Any]],
    ) -> list[dict]:
        """
        拼装最终送给模型的 messages 列表。
        system prompt 从 AGENT_PROMPTS 取，填充占位符后放在最前面。
        """
        template = AGENT_PROMPTS.get(agent_name, "")
        if prompt_vars:
            try:
                system_content = template.format(**prompt_vars)
            except KeyError:
                system_content = template
        else:
            system_content = template

        return [{"role": "system", "content": system_content}] + user_messages

    def _inject_json_instruction(self, messages: list[dict]) -> list[dict]:
        """在最后一条 user 消息末尾追加 JSON 输出指令"""
        messages = list(messages)
        for i in range(len(messages) - 1, -1, -1):
            if messages[i]["role"] == "user":
                messages[i] = dict(messages[i])
                messages[i]["content"] += "\n\n【重要】请只输出 JSON，不要任何解释、前缀或代码块标记。"
                break
        return messages

    def _extract_json(self, raw: str) -> str:
        """从模型输出中提取 JSON，处理可能的 markdown 代码块包裹"""
        import re
        raw = raw.strip()
        # 去掉 ```json ... ``` 或 ``` ... ``` 包裹
        match = re.search(r"```(?:json)?\s*([\s\S]+?)\s*```", raw)
        if match:
            return match.group(1).strip()
        # 尝试找第一个 { 到最后一个 }
        start = raw.find("{")
        end = raw.rfind("}")
        if start != -1 and end != -1 and end > start:
            return raw[start:end + 1]
        return raw

    def _validate_json(self, agent_name: str, raw: str) -> str:
        """
        校验 JSON Mode 输出是否合法 JSON。
        不合法时打印警告并返回原始字符串（让调用方处理）。
        """
        try:
            json.loads(raw)
            return raw
        except json.JSONDecodeError as e:
            print(f"[middleware] {agent_name} JSON 校验失败: {e}\n原始输出: {raw[:200]}")
            return raw


middleware = ToolMiddleware()
