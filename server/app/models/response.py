"""响应体 Schema — 对应接口文档的所有非流式响应"""
from datetime import datetime
from typing import Optional
from pydantic import BaseModel, Field

from app.models.product import SKU, FAQ


class SessionCreateResponse(BaseModel):
    session_id: str
    created_at: datetime


class ProductDetailResponse(BaseModel):
    product_id: str
    title: str
    brand: str
    category: str
    sub_category: str
    base_price: float
    image_url: str
    skus: list[SKU]
    marketing_description: str
    faq: list[FAQ] = Field(default_factory=list)


class CartItem(BaseModel):
    cart_item_id: str
    product_id: str
    sku_id: str
    title: str
    image_url: str
    sku_properties: dict[str, str]
    unit_price: float
    quantity: int
    subtotal: float


class CartResponse(BaseModel):
    session_id: str
    items: list[CartItem]
    total_count: int
    total_price: float


class CartAddResponse(BaseModel):
    cart_item_id: str
    message: str
    cart_total_count: int


class OrderResponse(BaseModel):
    order_id: str
    status: str
    message: str
    total_price: float
    created_at: datetime


class SessionInfo(BaseModel):
    session_id: str
    preview: str
    created_at: datetime
    updated_at: datetime


class SessionListResponse(BaseModel):
    sessions: list[SessionInfo]


class MessageInfo(BaseModel):
    role: str  # "user" | "assistant"
    content: str
    products: list[str] = Field(default_factory=list)
    timestamp: datetime


class MessageHistoryResponse(BaseModel):
    session_id: str
    messages: list[MessageInfo]


class ErrorResponse(BaseModel):
    code: str
    message: str
    detail: Optional[str] = None
