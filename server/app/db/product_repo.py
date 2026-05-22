"""
商品仓库 — 启动时把 100 条商品 JSON 全部加载进内存。
检索结果只返回 product_id，最终商品卡片字段从这里取，保证：
  1. 价格/图片等结构化数据不经大模型，杜绝幻觉
  2. O(1) 查询，比每次读文件快几个数量级
"""
import json
from pathlib import Path
from typing import Optional

from app.config import settings
from app.models import Product


class ProductRepository:
    def __init__(self):
        self._products: dict[str, Product] = {}

    def load(self) -> int:
        """从数据集目录加载全部商品 JSON"""
        self._products.clear()
        dataset_path = settings.dataset_path
        for category_dir in sorted(dataset_path.iterdir()):
            if not category_dir.is_dir():
                continue
            data_dir = category_dir / "data"
            if not data_dir.exists():
                continue
            for json_file in sorted(data_dir.glob("*.json")):
                with open(json_file, "r", encoding="utf-8") as f:
                    raw = json.load(f)
                p = Product.model_validate(raw)
                self._products[p.product_id] = p
        return len(self._products)

    def get(self, product_id: str) -> Optional[Product]:
        return self._products.get(product_id)

    def all(self) -> list[Product]:
        return list(self._products.values())

    def count(self) -> int:
        return len(self._products)


product_repo = ProductRepository()
