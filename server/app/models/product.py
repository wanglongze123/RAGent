"""商品数据模型 — 对应数据集 JSON 结构 + 接口响应"""
import re
from typing import Optional
from pydantic import BaseModel, Field

_UNITS = r"ml|mL|ML|L|g|G|kg|KG|mg|GB|MB|TB|cm|mm|克|升|毫升|%"
# 末尾规格：直接拼在词尾，无空格（美妆常见，如 "精华30ml"）
_TRAILING_SPEC_RE = re.compile(rf'\d+(?:\.\d+)?\s*(?:{_UNITS})\s*$')
# 中间规格：前面有空格（食品/饮料常见，如 " 445ml×15 瓶装..."）
_INLINE_SPEC_RE   = re.compile(rf'\s+\d+(?:\.\d+)?\s*(?:{_UNITS})')


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

    @property
    def display_title(self) -> str:
        """
        去掉规格信息的展示标题，用于商品卡片。
          步骤1：去掉末尾数字+单位（美妆：'...精华30ml' → '...精华'）
          步骤2：在第一个'空格+数字+单位'处截断（食品：'...饮料 445ml×...' → '...饮料'）
        """
        t = _TRAILING_SPEC_RE.sub("", self.title).strip()
        m = _INLINE_SPEC_RE.search(t)
        if m:
            t = t[:m.start()].strip()
        return t if len(t) >= 4 else self.title


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
