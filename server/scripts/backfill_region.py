"""
一次性脚本：为商品集合补 region 字段（日系/欧美/国产/韩系/东南亚/其他）。

为什么这么做：
  search_agent 的 _BRAND_CATEGORY_MAP 写死会随数据扩张过期，
  让数据自带地域信息后，硬过滤就能完全数据驱动。

用法：
  cd server && python scripts/backfill_region.py
  幂等：已有 region 字段的商品默认跳过；--force 全部重新打标。

逻辑：
  1. 扫数据集，收集所有唯一 brand
  2. 一次 LLM 调用对所有 brand 打地域标签（不挨个调）
  3. 回写每条 product JSON 的 region 字段
"""
import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import settings
from app.llm.client import llm_client


VALID_REGIONS = ["日系", "欧美", "国产", "韩系", "东南亚", "其他"]

PROMPT_TEMPLATE = """请把下列品牌按地域归类，每个品牌只能选 1 个：
{regions}

品牌列表：
{brands}

输出要求（严格遵守）：
- 只输出 JSON 对象，键是品牌名，值是地域名
- 不要任何解释、Markdown、代码块标记
- 没听过的或归不进去的，统一标"其他"

例：{{"雅诗兰黛": "欧美", "SK-II": "日系", "珀莱雅": "国产"}}
"""


def collect_brands(dataset_path: Path) -> tuple[set[str], list[Path]]:
    files = sorted(dataset_path.rglob("data/*.json"))
    brands: set[str] = set()
    for f in files:
        data = json.loads(f.read_text(encoding="utf-8"))
        b = data.get("brand", "").strip()
        if b:
            brands.add(b)
    return brands, files


def extract_json(raw: str) -> str:
    """复用 middleware._extract_json 的简化版"""
    raw = raw.strip()
    import re
    m = re.search(r"```(?:json)?\s*([\s\S]+?)\s*```", raw)
    if m:
        return m.group(1).strip()
    s, e = raw.find("{"), raw.rfind("}")
    if s != -1 and e > s:
        return raw[s : e + 1]
    return raw


async def classify_brands(brands: list[str]) -> dict[str, str]:
    prompt = PROMPT_TEMPLATE.format(
        regions="/".join(VALID_REGIONS),
        brands="\n".join(f"- {b}" for b in brands),
    )
    raw = await llm_client.chat(
        messages=[{"role": "user", "content": prompt}],
        temperature=0.0,
    )
    text = extract_json(raw)
    parsed: dict[str, str] = json.loads(text)
    # 校验 + 修复异常值
    cleaned: dict[str, str] = {}
    for b, r in parsed.items():
        if r not in VALID_REGIONS:
            print(f"[warn] {b} → {r} 不在合法地域内，改为'其他'")
            r = "其他"
        cleaned[b] = r
    return cleaned


def write_back(files: list[Path], brand_to_region: dict[str, str], force: bool) -> int:
    updated = 0
    for f in files:
        data = json.loads(f.read_text(encoding="utf-8"))
        brand = data.get("brand", "").strip()
        if not brand:
            continue
        if not force and data.get("region"):
            continue
        region = brand_to_region.get(brand)
        if not region:
            print(f"[skip] {f.name}: brand={brand!r} 未出现在分类结果")
            continue
        data["region"] = region
        f.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        updated += 1
    return updated


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--force",
        action="store_true",
        help="即使已有 region 也重新分类回写",
    )
    args = parser.parse_args()

    dataset_path = Path(settings.dataset_dir).resolve()
    print(f"[1/3] 扫描数据集: {dataset_path}")
    brands, files = collect_brands(dataset_path)
    print(f"     共 {len(files)} 个商品，{len(brands)} 个唯一品牌")

    print(f"[2/3] 调 LLM 一次性打标 …")
    brand_to_region = await classify_brands(sorted(brands))
    print(f"     LLM 返回 {len(brand_to_region)} 个映射")
    by_region: dict[str, list[str]] = {}
    for b, r in brand_to_region.items():
        by_region.setdefault(r, []).append(b)
    for r in VALID_REGIONS:
        if r in by_region:
            print(f"     {r}: {sorted(by_region[r])}")

    print(f"[3/3] 回写 product JSON ({'强制覆盖' if args.force else '幂等'})")
    n = write_back(files, brand_to_region, args.force)
    print(f"     更新 {n} 条")


if __name__ == "__main__":
    asyncio.run(main())
