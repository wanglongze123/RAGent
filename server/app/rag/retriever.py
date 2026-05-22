"""
检索器 — Phase 1 只实现纯向量检索，Phase 2 扩展为 Hybrid + Rerank。
"""
from typing import Optional, Any
from dataclasses import dataclass

from app.db.vector_store import get_vector_store, VectorStore
from app.llm.client import llm_client


@dataclass
class RetrievedChunk:
    """检索结果统一结构"""
    chunk_id: str
    product_id: str
    chunk_type: str  # base / description / faq / review
    content: str
    score: float
    metadata: dict[str, Any]


class VectorRetriever:
    """基础向量检索器"""

    def __init__(self, vector_store: Optional[VectorStore] = None):
        self._vs = vector_store or get_vector_store("products")

    async def retrieve(
        self,
        query: str,
        top_k: int = 8,
        where: Optional[dict[str, Any]] = None,
    ) -> list[RetrievedChunk]:
        """
        纯向量检索。
        - query:  用户原始查询
        - top_k:  返回多少个 chunk（注意是 chunk 数，不是商品数）
        - where:  metadata 过滤条件，如 {"category": "美妆护肤"}
                  或 {"$and": [{"category":"美妆护肤"}, {"base_price": {"$lt": 200}}]}
        """
        # 1) query embedding
        embeddings = await llm_client.embed_text([query])
        query_emb = embeddings[0]

        # 2) Chroma 向量检索
        raw_results = self._vs.query(
            query_embedding=query_emb,
            top_k=top_k,
            where=where,
        )

        # 3) 转成统一结构
        return [
            RetrievedChunk(
                chunk_id=r["id"],
                product_id=r["metadata"]["product_id"],
                chunk_type=r["metadata"].get("chunk_type", "unknown"),
                content=r["document"],
                score=r["score"],
                metadata=r["metadata"],
            )
            for r in raw_results
        ]

    async def retrieve_products(
        self,
        query: str,
        top_k_chunks: int = 12,
        top_k_products: int = 5,
        where: Optional[dict[str, Any]] = None,
    ) -> list[dict[str, Any]]:
        """
        商品级聚合 — 一个商品多个 chunk 命中时合并打分。
        返回 [{product_id, score, hit_chunks: [...]}]，按 score 降序。
        """
        chunks = await self.retrieve(query, top_k=top_k_chunks, where=where)

        # 按 product_id 聚合，取最高分作为商品分（也可改为 sum）
        product_map: dict[str, dict] = {}
        for c in chunks:
            pid = c.product_id
            if pid not in product_map:
                product_map[pid] = {
                    "product_id": pid,
                    "score": c.score,
                    "hit_chunks": [c],
                    "metadata": c.metadata,
                }
            else:
                product_map[pid]["score"] = max(product_map[pid]["score"], c.score)
                product_map[pid]["hit_chunks"].append(c)

        ranked = sorted(product_map.values(), key=lambda x: x["score"], reverse=True)
        return ranked[:top_k_products]


# 全局单例
retriever = VectorRetriever()
