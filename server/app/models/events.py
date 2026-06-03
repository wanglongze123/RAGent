"""
SSE 事件流 Schema — 对应接口文档第 6 节。
所有 Agent 输出最终都转成这里的某个事件类型推给客户端。
"""
import json
from enum import Enum
from typing import Any, Optional
from pydantic import BaseModel, Field


class EventType(str, Enum):
    THINKING = "thinking"
    TOOL_PROGRESS = "tool_progress"
    TEXT_DELTA = "text_delta"
    PRODUCT_CARD = "product_card"
    PRODUCT_CARD_LIST = "product_card_list"
    COMPARISON_TABLE = "comparison_table"
    CART_UPDATE = "cart_update"
    CLARIFICATION = "clarification"
    IMAGE_SEARCHING = "image_searching"
    ORDER_FORM = "order_form"
    ERROR = "error"
    DONE = "done"


class SSEEvent(BaseModel):
    """SSE 事件统一结构 — to_sse() 生成符合协议的字符串"""
    type: EventType
    data: dict[str, Any] = Field(default_factory=dict)

    def to_sse(self) -> str:
        payload = json.dumps(self.data, ensure_ascii=False)
        return f"event: {self.type.value}\ndata: {payload}\n\n"


# ===== 各事件 data 的类型化构造（业务代码用这些工厂方法生成事件）=====

def thinking(message: str = "正在理解您的需求...") -> SSEEvent:
    return SSEEvent(type=EventType.THINKING, data={"message": message})


def tool_progress(tool: str, message: str) -> SSEEvent:
    return SSEEvent(type=EventType.TOOL_PROGRESS, data={"tool": tool, "message": message})


def text_delta(text: str) -> SSEEvent:
    return SSEEvent(type=EventType.TEXT_DELTA, data={"text": text})


def product_card(
    product_id: str,
    title: str,
    brand: str,
    image_url: str,
    price: float,
    sub_category: str,
    reason: Optional[str] = None,
    similarity_score: Optional[float] = None,
) -> SSEEvent:
    data = {
        "product_id": product_id,
        "title": title,
        "brand": brand,
        "image_url": image_url,
        "price": price,
        "sub_category": sub_category,
    }
    if reason is not None:
        data["reason"] = reason
    if similarity_score is not None:
        data["similarity_score"] = similarity_score
    return SSEEvent(type=EventType.PRODUCT_CARD, data=data)


def product_card_list(products: list[dict], search_type: str = "text") -> SSEEvent:
    return SSEEvent(
        type=EventType.PRODUCT_CARD_LIST,
        data={"products": products, "search_type": search_type},
    )


def comparison_table(
    products: list[dict],
    dimensions: list[dict],
    recommendation: Optional[dict] = None,
) -> SSEEvent:
    """
    多商品结构化对比表。
      products:       [{product_id, title, price, image_url}]（数据全来自 product_repo，不由模型生成）
      dimensions:     [{name, values:[...]}]，values 顺序与 products 对齐
      recommendation: {product_id, reason} 或 None
    """
    data: dict = {"products": products, "dimensions": dimensions}
    if recommendation is not None:
        data["recommendation"] = recommendation
    return SSEEvent(type=EventType.COMPARISON_TABLE, data=data)


def cart_update(
    action: str,  # "add" | "remove" | "update_quantity" | "checkout"（下单后清空）
    product_id: str,
    sku_id: str,
    title: str,
    quantity: int,
    cart_total_count: int,
    cart_total_price: float,
    message: str,
) -> SSEEvent:
    return SSEEvent(
        type=EventType.CART_UPDATE,
        data={
            "action": action,
            "product_id": product_id,
            "sku_id": sku_id,
            "title": title,
            "quantity": quantity,
            "cart_total_count": cart_total_count,
            "cart_total_price": cart_total_price,
            "message": message,
        },
    )


def image_searching(message: str = "正在分析图片…") -> SSEEvent:
    return SSEEvent(type=EventType.IMAGE_SEARCHING, data={"message": message})


def clarification(question: str, options: list[str]) -> SSEEvent:
    return SSEEvent(
        type=EventType.CLARIFICATION,
        data={"question": question, "options": options},
    )


def order_form(saved_addresses: list[dict]) -> SSEEvent:
    """弹起收货信息表单，可附带历史地址供前端一键填入（原始手机号，前端自行脱敏）。"""
    return SSEEvent(
        type=EventType.ORDER_FORM,
        data={"saved_addresses": saved_addresses},
    )


def error(code: str, message: str) -> SSEEvent:
    return SSEEvent(type=EventType.ERROR, data={"code": code, "message": message})


def done(session_id: str, agent_state: str = "browsing") -> SSEEvent:
    return SSEEvent(
        type=EventType.DONE,
        data={"session_id": session_id, "agent_state": agent_state},
    )
