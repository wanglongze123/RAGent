"""
导购会话状态机 — 定义 4 个导购阶段、转移规则、每个状态允许的子 Agent。

为什么用状态机而不让大模型自由决策：
  电商导购对确定性要求高——下单过程中不能随意跳到搜索，
  搜索过程中加购应该先进购物车态再说。
  状态机把这些业务规则写死，大模型只负责意图分类，
  "能不能做这件事"由状态机决定，不是模型拍脑袋。
"""
from enum import Enum


class AgentState(str, Enum):
    BROWSING          = "browsing"           # 浏览/搜索商品
    COMPARING         = "comparing"          # 对比多个商品
    CART_MANAGEMENT   = "cart_management"    # 管理购物车
    CHECKOUT          = "checkout"           # 填写信息/确认下单


# ───── 每个状态允许路由的子 Agent ─────
# 限制工具空间：子 Agent 越少，模型路由出错概率越低
STATE_ALLOWED_AGENTS: dict[AgentState, list[str]] = {
    AgentState.BROWSING:        ["search", "compare"],
    AgentState.COMPARING:       ["search", "compare"],
    AgentState.CART_MANAGEMENT: ["cart", "search"],
    AgentState.CHECKOUT:        ["order"],   # 下单态严格限定，防止中途跑偏
}

# ───── 意图 → 下一个状态的转移规则 ─────
# key: (当前状态, 意图)  value: 下一个状态
# 未列出的组合 = 状态不变
TRANSITIONS: dict[tuple[AgentState, str], AgentState] = {
    # 从浏览态出发
    (AgentState.BROWSING,        "compare"):      AgentState.COMPARING,
    (AgentState.BROWSING,        "cart_add"):      AgentState.CART_MANAGEMENT,
    (AgentState.BROWSING,        "checkout"):      AgentState.CHECKOUT,

    # 从对比态出发
    (AgentState.COMPARING,       "search"):        AgentState.BROWSING,
    (AgentState.COMPARING,       "cart_add"):      AgentState.CART_MANAGEMENT,
    (AgentState.COMPARING,       "checkout"):      AgentState.CHECKOUT,

    # 从购物车态出发
    (AgentState.CART_MANAGEMENT, "search"):        AgentState.BROWSING,
    (AgentState.CART_MANAGEMENT, "compare"):       AgentState.COMPARING,
    (AgentState.CART_MANAGEMENT, "checkout"):      AgentState.CHECKOUT,

    # 从下单态出发
    (AgentState.CHECKOUT,        "order_confirmed"): AgentState.BROWSING,
    (AgentState.CHECKOUT,        "user_cancel"):      AgentState.CART_MANAGEMENT,
}


def get_next_state(current: AgentState, intent: str) -> AgentState:
    """根据当前状态和意图返回下一个状态，无匹配则状态不变"""
    return TRANSITIONS.get((current, intent), current)


def get_allowed_agents(state: AgentState) -> list[str]:
    """返回当前状态允许调用的子 Agent 列表"""
    return STATE_ALLOWED_AGENTS.get(state, ["search"])


def is_agent_allowed(state: AgentState, agent_name: str) -> bool:
    return agent_name in get_allowed_agents(state)
