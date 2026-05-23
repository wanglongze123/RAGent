"""
数据入库脚本 — 一次性运行，把 100 条商品 JSON 切 chunk、embedding、存进 Chroma。

用法：
    cd server
    python -m scripts.build_index                 # 增量：只补已有 collection 缺的
    python -m scripts.build_index --rebuild       # 全量重建文本索引：先 reset 再灌
    python -m scripts.build_index --with-images   # 同时建/更新图片索引（独立 collection）
    python -m scripts.build_index --images-only   # 只建图片索引，不动文本

Chunking 策略（数据集天然分层）：
  - 1 个 chunk: marketing_description（核心卖点长文）
  - N 个 chunk: 每条 official_faq（独立 Q-A 对）
  - N 个 chunk: 每条 user_review（独立用户评价）

每个 chunk 入库时 metadata 包含 product_id / chunk_type / category / brand /
sub_category / price，方便后续做 metadata 过滤（价格区间、品牌排除等）。

图片索引（--with-images）：
  - 独立 collection: product_images（与文本 collection 分开）
  - 每个商品 1 个图向量（embed_image(image_path)）
  - id: {product_id}_image
  - Doubao 文本和图片走同一 multimodal endpoint，向量空间对齐
"""
import argparse
import asyncio
import base64
import json
import sys
from pathlib import Path

# 让脚本能从 server/ 根目录直接运行
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import settings
from app.llm.client import llm_client
from app.db.vector_store import get_vector_store
from app.models import Product


# 一次 embedding 调多少条 — 豆包 batch 限制 + 网络稳定性折中
BATCH_SIZE = 16


def load_all_products() -> list[Product]:
    """遍历数据集目录，反序列化所有商品 JSON"""
    products: list[Product] = []
    dataset_path = settings.dataset_path
    if not dataset_path.exists():
        raise FileNotFoundError(f"数据集目录不存在: {dataset_path}")

    for category_dir in sorted(dataset_path.iterdir()):
        if not category_dir.is_dir():
            continue
        data_dir = category_dir / "data"
        if not data_dir.exists():
            continue
        for json_file in sorted(data_dir.glob("*.json")):
            with open(json_file, "r", encoding="utf-8") as f:
                raw = json.load(f)
            products.append(Product.model_validate(raw))

    return products


def chunk_product(product: Product) -> list[dict]:
    """
    把单个商品按数据集天然分层切成多个 chunk。
    每个 chunk = {id, text, metadata}
    """
    chunks: list[dict] = []
    base_meta = {
        "product_id": product.product_id,
        "title": product.title,
        "brand": product.brand,
        "category": product.category,
        "sub_category": product.sub_category,
        "base_price": product.base_price,
    }

    # 商品基础信息单独一个 chunk — 用户用品牌名+品类搜索时这条最易命中
    chunks.append({
        "id": f"{product.product_id}_base",
        "text": f"{product.title}\n品牌:{product.brand}\n类目:{product.category}/{product.sub_category}\n价格:{product.base_price}元",
        "metadata": {**base_meta, "chunk_type": "base"},
    })

    if product.rag_knowledge is None:
        return chunks

    # 营销描述 — 检索"功效/适用人群/卖点"时最有用
    chunks.append({
        "id": f"{product.product_id}_desc",
        "text": product.rag_knowledge.marketing_description,
        "metadata": {**base_meta, "chunk_type": "description"},
    })

    # 每条 FAQ 独立 chunk — 用户问具体问题时高质量命中
    for i, faq in enumerate(product.rag_knowledge.official_faq):
        chunks.append({
            "id": f"{product.product_id}_faq_{i}",
            "text": f"Q: {faq.question}\nA: {faq.answer}",
            "metadata": {**base_meta, "chunk_type": "faq"},
        })

    # 每条评价独立 chunk — 用户问"敏感肌能用吗"等真实体验问题时命中
    for i, review in enumerate(product.rag_knowledge.user_reviews):
        chunks.append({
            "id": f"{product.product_id}_review_{i}",
            "text": f"用户评价({review.rating}星): {review.content}",
            "metadata": {
                **base_meta,
                "chunk_type": "review",
                "review_rating": review.rating,
            },
        })

    return chunks


async def build_index(rebuild: bool = False):
    print(f"[1/4] 加载数据集: {settings.dataset_path}")
    products = load_all_products()
    print(f"  ✓ 共 {len(products)} 个商品")

    print(f"[2/4] 切 chunk")
    all_chunks: list[dict] = []
    for p in products:
        all_chunks.extend(chunk_product(p))
    print(f"  ✓ 共 {len(all_chunks)} 个 chunk")

    print(f"[3/4] 初始化向量库（{settings.vector_store}）")
    vs = get_vector_store("products")
    if rebuild:
        vs.reset()
        print("  ✓ 已清空 collection")
    print(f"  ✓ 当前 collection 数量: {vs.count()}")

    print(f"[4/4] 调豆包 embedding API 批量入库（batch={BATCH_SIZE}）")
    total = len(all_chunks)
    for i in range(0, total, BATCH_SIZE):
        batch = all_chunks[i:i + BATCH_SIZE]
        texts = [c["text"] for c in batch]
        embeddings = await llm_client.embed_text(texts)
        vs.add(
            ids=[c["id"] for c in batch],
            embeddings=embeddings,
            documents=texts,
            metadatas=[c["metadata"] for c in batch],
        )
        print(f"  [{min(i + BATCH_SIZE, total)}/{total}] 已入库")

    print(f"\n✅ 完成。最终 collection 数量: {vs.count()}")


async def build_image_index(rebuild: bool = False):
    """
    为每个商品的 live 图建独立 chroma collection: product_images。
    Doubao 多模态 endpoint 让图片向量和文本向量在同一空间，所以图搜命中后
    可以直接用商品 id 关联回 product_repo / 文本资料。
    """
    print(f"[image 1/3] 加载数据集: {settings.dataset_path}")
    products = load_all_products()
    print(f"  ✓ 共 {len(products)} 个商品")

    print(f"[image 2/3] 初始化图片向量库 (collection=product_images)")
    vs_img = get_vector_store("product_images")
    if rebuild:
        vs_img.reset()
        print("  ✓ 已清空 collection")
    print(f"  ✓ 当前数量: {vs_img.count()}")

    print(f"[image 3/3] 逐商品 embed_image 并入库")
    skipped, embedded, missing = 0, 0, 0
    for i, p in enumerate(products, start=1):
        img_path = settings.dataset_path / p.image_path
        if not img_path.exists():
            print(f"  [{i}/{len(products)}] {p.product_id} 图片缺失: {img_path}")
            missing += 1
            continue
        try:
            with open(img_path, "rb") as f:
                b64 = base64.b64encode(f.read()).decode("ascii")
            vec = await llm_client.embed_image(b64, mime_type="image/jpeg")
            vs_img.add(
                ids=[f"{p.product_id}_image"],
                embeddings=[vec],
                # document 字段存"商品标题"做兜底人类可读，纯检索时用不到
                documents=[p.title],
                metadatas=[{
                    "product_id": p.product_id,
                    "title": p.title,
                    "brand": p.brand,
                    "category": p.category,
                    "sub_category": p.sub_category,
                    "base_price": p.base_price,
                    "region": p.region or "",
                }],
            )
            embedded += 1
            print(f"  [{i}/{len(products)}] {p.product_id} ✓")
        except Exception as e:
            skipped += 1
            print(f"  [{i}/{len(products)}] {p.product_id} ✗ {e}")

    print(f"\n✅ 图片索引完成: 入库 {embedded}，跳过 {skipped}，缺图 {missing}")
    print(f"   collection 最终数量: {vs_img.count()}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--rebuild", action="store_true", help="全量重建文本索引")
    parser.add_argument("--with-images", action="store_true", help="同时建/更新图片索引")
    parser.add_argument("--images-only", action="store_true", help="只建图片索引")
    args = parser.parse_args()

    async def _main():
        if not args.images_only:
            await build_index(rebuild=args.rebuild)
        if args.with_images or args.images_only:
            await build_image_index(rebuild=args.rebuild)

    asyncio.run(_main())
