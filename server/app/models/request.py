"""请求体 Schema — 对应接口文档的 POST/PUT 入参"""
from typing import Optional
from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    session_id: str
    message: str
    image_base64: Optional[str] = None


class ImageSearchRequest(BaseModel):
    session_id: str
    image_base64: str
    image_mime_type: str = "image/jpeg"


class CartAddRequest(BaseModel):
    session_id: str
    product_id: str
    sku_id: str
    quantity: int = Field(default=1, gt=0)


class CartUpdateRequest(BaseModel):
    session_id: str
    quantity: int = Field(gt=0)


class OrderItemRequest(BaseModel):
    product_id: str
    sku_id: str
    quantity: int
    unit_price: float


class OrderSubmitRequest(BaseModel):
    session_id: str
    receiver_name: str
    receiver_phone: str
    receiver_address: str
    items: list[OrderItemRequest]
    total_price: float
