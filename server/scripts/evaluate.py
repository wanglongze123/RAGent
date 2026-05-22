"""
RAG 检索质量评估脚本

用法：
    cd server
    python -m scripts.evaluate                  # 评估当前检索器
    python -m scripts.evaluate --top-k 1 3 5   # 自定义 K 值

输出指标：
    Recall@K  — Top-K 结果里至少命中一个相关商品的查询比例
    MRR       — 第一个相关商品排名的倒数均值（衡量排第几）
    NDCG@K    — 综合排序质量（相关商品排越靠前分越高）

消融实验用法：
    每次加完一个模块（BM25/Reranker/QueryRewrite），
    重跑此脚本，把输出贴到 phase2_summary.md 对比表里。
"""
import argparse
import asyncio
import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db.vector_store import get_vector_store
from app.rag.bm25_retriever import bm25_retriever
from app.rag.reranker import reranker
from app.rag.hybrid_retriever import hybrid_retriever as retriever

# 评估前先构建 BM25 索引 + 加载 Reranker
_vs = get_vector_store("products")
if _vs.count() > 0:
    bm25_retriever.build_from_chroma(_vs._collection)
reranker.load()


def recall_at_k(retrieved_ids: list[str], relevant_ids: list[str], k: int) -> float:
    """Top-K 内是否命中至少一个相关商品"""
    top_k = set(retrieved_ids[:k])
    return 1.0 if top_k & set(relevant_ids) else 0.0


def reciprocal_rank(retrieved_ids: list[str], relevant_ids: list[str]) -> float:
    """第一个相关商品出现在第几位（1-indexed 倒数）"""
    relevant_set = set(relevant_ids)
    for rank, pid in enumerate(retrieved_ids, start=1):
        if pid in relevant_set:
            return 1.0 / rank
    return 0.0


def ndcg_at_k(retrieved_ids: list[str], relevant_ids: list[str], k: int) -> float:
    """
    NDCG@K — 二元相关度版本。
    相关商品出现越靠前，得分越高（对数折扣）。
    """
    relevant_set = set(relevant_ids)

    def dcg(ids: list[str]) -> float:
        return sum(
            1.0 / math.log2(rank + 1)
            for rank, pid in enumerate(ids[:k], start=1)
            if pid in relevant_set
        )

    actual_dcg = dcg(retrieved_ids)
    # 理想情况：所有相关商品都排在最前面
    ideal_ids = [pid for pid in retrieved_ids if pid in relevant_set] + \
                [pid for pid in retrieved_ids if pid not in relevant_set]
    ideal_dcg = dcg(ideal_ids)

    return actual_dcg / ideal_dcg if ideal_dcg > 0 else 0.0


async def run_evaluation(top_ks: list[int] = [1, 3, 5]) -> dict:
    dataset_path = Path(__file__).parent / "eval_dataset.json"
    with open(dataset_path, "r", encoding="utf-8") as f:
        dataset = json.load(f)

    max_k = max(top_ks)
    results = []

    print(f"评估中... 共 {len(dataset)} 条查询\n")
    for item in dataset:
        ranked = await retriever.retrieve_products(
            query=item["query"],
            top_k_chunks=max_k * 3,
            top_k_products=max_k,
        )
        retrieved_ids = [r["product_id"] for r in ranked]

        row = {
            "query_id": item["query_id"],
            "query": item["query"],
            "category": item["category"],
            "difficulty": item["difficulty"],
            "retrieved": retrieved_ids,
            "relevant": item["relevant_ids"],
            "mrr": reciprocal_rank(retrieved_ids, item["relevant_ids"]),
        }
        for k in top_ks:
            row[f"recall@{k}"] = recall_at_k(retrieved_ids, item["relevant_ids"], k)
            row[f"ndcg@{k}"] = ndcg_at_k(retrieved_ids, item["relevant_ids"], k)
        results.append(row)

    return results


def print_report(results: list[dict], top_ks: list[int]):
    n = len(results)

    # ===== 总体指标 =====
    print("=" * 60)
    print("总体指标")
    print("=" * 60)
    metrics = {}
    for k in top_ks:
        metrics[f"Recall@{k}"] = sum(r[f"recall@{k}"] for r in results) / n
        metrics[f"NDCG@{k}"] = sum(r[f"ndcg@{k}"] for r in results) / n
    metrics["MRR"] = sum(r["mrr"] for r in results) / n

    for name, val in metrics.items():
        bar = "█" * int(val * 20)
        print(f"  {name:<12} {val:.4f}  {bar}")
    print()

    # ===== 按类目分组 =====
    print("=" * 60)
    print("按类目 Recall@5")
    print("=" * 60)
    categories = {}
    for r in results:
        cat = r["category"]
        categories.setdefault(cat, []).append(r[f"recall@{max(top_ks)}"])
    for cat, vals in categories.items():
        avg = sum(vals) / len(vals)
        print(f"  {cat:<12} {avg:.4f}  ({len(vals)} 条)")
    print()

    # ===== 失败案例 =====
    fails = [r for r in results if r[f"recall@{max(top_ks)}"] == 0]
    if fails:
        print("=" * 60)
        print(f"未命中案例（Top-{max(top_ks)} 内未找到相关商品，共 {len(fails)} 条）")
        print("=" * 60)
        for r in fails:
            print(f"  [{r['query_id']}] {r['query']}")
            print(f"    期望: {r['relevant']}")
            print(f"    实际: {r['retrieved'][:max(top_ks)]}")
    else:
        print(f"✅ 全部 {n} 条查询 Top-{max(top_ks)} 均命中！")

    print()
    print("=" * 60)
    print("消融实验记录（复制到 phase2_summary.md）")
    print("=" * 60)
    row = "| 当前方案 |"
    for k in top_ks:
        row += f" {metrics[f'Recall@{k}']:.4f} |"
    row += f" {metrics['MRR']:.4f} |"
    row += f" {metrics[f'NDCG@{max(top_ks)}']:.4f} |"
    print(row)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--top-k", nargs="+", type=int, default=[1, 3, 5])
    args = parser.parse_args()

    results = asyncio.run(run_evaluation(top_ks=args.top_k))
    print_report(results, top_ks=args.top_k)
