"""商品数据模型 — 对应数据集 JSON 结构 + 接口响应"""
from typing import Optional
from pydantic import BaseModel, Field


class SKU(BaseModel):
    sku_id: str
    properties: dict[str, str] = Field(default_factory=dict)
    price: float
    stock: int = 99


class FAQ(BaseModel):
    question: str
    answer: str


class UserReview(BaseModel):
    nickname: str
    rating: int
    content: str


class RagKnowledge(BaseModel):
    marketing_description: str
    official_faq: list[FAQ] = Field(default_factory=list)
    user_reviews: list[UserReview] = Field(default_factory=list)


class Product(BaseModel):
    """完整商品信息 — 数据集 JSON 反序列化目标"""
    product_id: str
    title: str
    brand: str
    category: str
    sub_category: str
    base_price: float
    image_path: str
    region: Optional[str] = None  # "日系" / "欧美" / "国产" / "韩系" 等，用于硬过滤
    skus: list[SKU] = Field(default_factory=list)
    rag_knowledge: Optional[RagKnowledge] = None

    @property
    def image_url(self) -> str:
        """对外暴露的图片URL路径（客户端拼接 base_url）"""
        return f"/static/images/{self.image_path}"


class ProductCard(BaseModel):
    """商品卡片 — SSE 推送给客户端的精简结构"""
    product_id: str
    title: str
    brand: str
    image_url: str
    price: float
    sub_category: str
    reason: Optional[str] = None
    similarity_score: Optional[float] = None
