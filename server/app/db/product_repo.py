"""
商品仓库 — 启动时把 100 条商品 JSON 全部加载进内存。
检索结果只返回 product_id，最终商品卡片字段从这里取，保证：
  1. 价格/图片等结构化数据不经大模型，杜绝幻觉
  2. O(1) 查询，比每次读文件快几个数量级
"""
import json
from collections import defaultdict
from pathlib import Path
from typing import Optional

from app.config import settings
from app.models import Product


# 用户表达 → 标准 region 的别名（同义词归一）
# 把"国货""国内""中国"这种用户口语等同到数据里的"国产"
# 数据里没有的标签（比如"美系""欧系"分别归"欧美"）也在这里收口
_REGION_ALIASES: dict[str, str] = {
    "国货": "国产",
    "国内": "国产",
    "中国": "国产",
    "美系": "欧美",
    "欧系": "欧美",
}


class ProductRepository:
    def __init__(self):
        self._products: dict[str, Product] = {}
        self._brands_by_region: dict[str, list[str]] = {}
        self._regions: set[str] = set()

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
        self._build_region_index()
        return len(self._products)

    def _build_region_index(self) -> None:
        """加载完成后聚合一次，避免每次过滤都遍历全表"""
        by_region: dict[str, set[str]] = defaultdict(set)
        for p in self._products.values():
            if p.region and p.brand:
                by_region[p.region].add(p.brand)
        self._brands_by_region = {r: sorted(bs) for r, bs in by_region.items()}
        self._regions = set(self._brands_by_region.keys())

    def get(self, product_id: str) -> Optional[Product]:
        return self._products.get(product_id)

    def all(self) -> list[Product]:
        return list(self._products.values())

    def count(self) -> int:
        return len(self._products)

    # ───── 地域硬过滤索引 ─────

    def regions(self) -> set[str]:
        """所有商品里出现过的 region 集合（不含别名，是数据里的真实标签）"""
        return set(self._regions)

    def brands_in_region(self, region_label: str) -> list[str]:
        """
        给一个用户/LLM 输入的地域词（含别名），返回该地域下的所有品牌。
        没匹配到返回空列表，调用方按字面品牌名过滤即可。
        """
        canonical = _REGION_ALIASES.get(region_label, region_label)
        return list(self._brands_by_region.get(canonical, []))

    def all_region_keywords(self) -> set[str]:
        """所有可触发"不要XX"的地域关键词 = 真实 region + 别名键"""
        return self._regions | set(_REGION_ALIASES.keys())


product_repo = ProductRepository()
