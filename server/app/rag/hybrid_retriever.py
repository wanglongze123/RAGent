"""
混合检索器 — 向量召回 + BM25 召回 → RRF 融合 → 商品聚合。

RRF（Reciprocal Rank Fusion）公式：
    score(d) = Σ 1 / (k + rank(d))，k=60
    k 不是无参，是对 k 不敏感——k 在 10-100 之间性能差异很小。
    RRF 真正的优雅在于：向量分数（0-1 余弦）和 BM25 分数（无上界）
    量纲完全不同，直接加权融合需要归一化很难调；
    RRF 只用排名，完全绕开了量纲问题。

Query 解构：
    "200元以内的洗面奶" 拆成：
      语义部分  → "洗面奶"   → 走 RAG 检索
      结构化部分 → price < 200 → 走 metadata filter
    价格约束在向量空间里是噪声，必须走结构化过滤才可靠。
"""
import re
import time
from typing import Any, Optional
from dataclasses import dataclass, field

from app.db.vector_store import get_vector_store
from app.db.cache import cache_get, cache_set
from app.llm.client import llm_client
from app.rag.bm25_retriever import bm25_retriever
from app.rag.query_expander import expand_query
from app.rag.retriever import RetrievedChunk
from app.rag.reranker import reranker


RRF_K = 60  # 默认 60，工业界常用值，对此值不敏感但并非无参


@dataclass
class ParsedQuery:
    """Query 解构结果"""
    semantic_query: str                          # 语义部分 → RAG
    where_filter: Optional[dict[str, Any]] = None  # 结构化部分 → metadata filter


# 价格关键词正则 — 覆盖常见中文表达
_PRICE_PATTERNS = [
    (r"(\d+)\s*元以内",        lambda m: {"base_price": {"$lte": float(m.group(1))}}),
    (r"(\d+)\s*元以下",        lambda m: {"base_price": {"$lt":  float(m.group(1))}}),
    (r"(\d+)\s*元以上",        lambda m: {"base_price": {"$gte": float(m.group(1))}}),
    (r"预算\s*(\d+)",          lambda m: {"base_price": {"$lte": float(m.group(1))}}),
    (r"(\d+)\s*-\s*(\d+)\s*元", lambda m: {"$and": [
        {"base_price": {"$gte": float(m.group(1))}},
        {"base_price": {"$lte": float(m.group(2))}},
    ]}),
]

_PRICE_REMOVE_PATTERN = re.compile(
    r"\d+\s*元(以内|以下|以上)|\d+\s*-\s*\d+\s*元|预算\s*\d+"
)


def parse_query(query: str) -> ParsedQuery:
    """
    从 query 中提取结构化约束，返回纯语义 query + metadata filter。
    目前支持价格过滤，Phase 3 Search Agent 会扩展品牌排除等。
    """
    where_filter = None
    semantic = query

    for pattern, builder in _PRICE_PATTERNS:
        m = re.search(pattern, query)
        if m:
            where_filter = builder(m)
            semantic = _PRICE_REMOVE_PATTERN.sub("", query).strip()
            break

    return ParsedQuery(semantic_query=semantic or query, where_filter=where_filter)


def _rrf_fuse(
    *rankings: list[dict[str, Any]],
    k: int = RRF_K,
) -> list[tuple[str, float]]:
    """
    RRF 融合多路检索结果。
    每路 rankings 是 [{id, document, metadata, score}, ...] 按相关度降序。
    返回 [(chunk_id, rrf_score), ...] 降序。
    """
    scores: dict[str, float] = {}
    for ranking in rankings:
        for rank, item in enumerate(ranking, start=1):
            doc_id = item["id"]
            scores[doc_id] = scores.get(doc_id, 0.0) + 1.0 / (k + rank)
    return sorted(scores.items(), key=lambda x: x[1], reverse=True)


class HybridRetriever:
    """
    混合检索器 — 对外接口与 VectorRetriever 保持一致，可无缝替换。
    """

    def __init__(self):
        self._vs = get_vector_store("products")
        # 图片向量库延迟初始化，因为可能没建（避免启动报错）
        self._vs_images = None

    def _get_image_store(self):
        if self._vs_images is None:
            self._vs_images = get_vector_store("product_images")
        return self._vs_images

    async def retrieve(
        self,
        query: str,
        top_k: int = 10,
        where: Optional[dict[str, Any]] = None,
    ) -> list[RetrievedChunk]:
        """chunk 级检索，供 Reranker 使用"""
        parsed = parse_query(query)
        effective_where = where or parsed.where_filter

        # 两路并行召回（Python 协程 + 同步 BM25，实际已足够快）
        t0 = time.time()
        embeddings = await llm_client.embed_text([parsed.semantic_query])
        print(f"[perf] embed_text: {time.time()-t0:.3f}s", flush=True)
        t1 = time.time()
        vector_results = self._vs.query(
            query_embedding=embeddings[0],
            top_k=top_k * 2,
            where=effective_where,
        )
        # BM25 用同义词扩展后的 query，解决字面词不匹配问题
        expanded_query = expand_query(parsed.semantic_query)
        bm25_results = bm25_retriever.search(
            query=expanded_query,
            top_k=top_k * 2,
            where=effective_where,
        )

        print(f"[perf] vector+bm25 search: {time.time()-t1:.3f}s", flush=True)
        # RRF 融合
        fused = _rrf_fuse(vector_results, bm25_results, k=RRF_K)

        # 构建 id → 原始数据的索引（取 vector / BM25 任意一侧均可）
        id_to_data: dict[str, dict] = {}
        for r in vector_results + bm25_results:
            id_to_data[r["id"]] = r

        results = []
        for chunk_id, rrf_score in fused[:top_k]:
            data = id_to_data.get(chunk_id)
            if not data:
                continue
            results.append(RetrievedChunk(
                chunk_id=chunk_id,
                product_id=data["metadata"]["product_id"],
                chunk_type=data["metadata"].get("chunk_type", "unknown"),
                content=data["document"],
                score=rrf_score,
                metadata=data["metadata"],
            ))
        return results

    async def retrieve_products(
        self,
        query: str,
        top_k_chunks: int = 15,
        top_k_products: int = 5,
        where: Optional[dict[str, Any]] = None,
    ) -> list[dict[str, Any]]:
        """
        商品级聚合 + Reranker 精排（对外主入口）。
        Redis 缓存：相同 query + filter 直接返回缓存，无 TTL（数据集静态）。
        """
        cached = await cache_get("retrieve", query=query, where=where, top_k=top_k_products)
        if cached is not None:
            print(f"[cache] HIT: {query[:30]}", flush=True)
            return cached

        chunks = await self.retrieve(query, top_k=top_k_chunks, where=where)

        # 1. 先聚合到商品级别
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
        candidates = ranked[:top_k_products * 2]  # 控制 reranker 候选数，避免候选过多导致精排超时

        # 2. 商品级 Reranker：用"标题 + 最相关 chunk"代表每个商品
        product_chunks = [
            RetrievedChunk(
                chunk_id=p["product_id"],
                product_id=p["product_id"],
                chunk_type="product_repr",
                # 标题 + 最高分 chunk 内容，给 BGE 更完整的商品语义
                content=f"商品：{p['metadata'].get('title', '')}\n{p['hit_chunks'][0].content}",
                score=p["score"],
                metadata=p["metadata"],
            )
            for p in candidates
        ]

        reranked_chunks = await reranker.rerank(query, product_chunks, top_k=top_k_products)

        # 3. 还原回 product_map 结构
        #    hit_chunks 仅用于上面拼 reranker 输入，下游不消费；这里剥掉，
        #    否则 RetrievedChunk(dataclass) 无法 JSON 序列化，会导致缓存写入失败。
        reranked_products = []
        for rc in reranked_chunks:
            p = next((x for x in candidates if x["product_id"] == rc.product_id), None)
            if p:
                p["score"] = rc.score
                p.pop("hit_chunks", None)
                reranked_products.append(p)

        await cache_set("retrieve", reranked_products, query=query, where=where, top_k=top_k_products)
        return reranked_products


    async def retrieve_by_image(
        self,
        image_base64: str,
        top_k: int = 5,
        where: Optional[dict[str, Any]] = None,
        mime_type: str = "image/jpeg",
    ) -> list[dict[str, Any]]:
        """
        以图搜图：用户上传图 → embed_image → 查 product_images collection。

        与文本检索的关键差异：
          - 不走 BM25（图片没文本）
          - 不走 reranker（BGE 是文本 reranker，图文打分算不出来）
          - 不走 chunk 聚合（每个商品本来就只有一张图）
        所以这条管道是单路向量召回，命中后直接按相似度返回 product_id。

        返回结构与 retrieve_products 兼容：
          [{"product_id", "score", "metadata", "hit_chunks": []}]
        hit_chunks 留空，调用方（search_agent）想拿文本资料的话可以再去 product_repo 取。
        """
        vs_img = self._get_image_store()
        if vs_img.count() == 0:
            # 图片索引为空 → 上层应该提示用户跑 build_index --with-images
            return []

        vec = await llm_client.embed_image(image_base64, mime_type=mime_type)
        hits = vs_img.query(
            query_embedding=vec,
            top_k=top_k,
            where=where,
        )
        # 同一 product 在 image collection 里只有 1 条，所以不需去重
        return [
            {
                "product_id": h["metadata"]["product_id"],
                "score": h["score"],
                "metadata": h["metadata"],
                "hit_chunks": [],
            }
            for h in hits
        ]


hybrid_retriever = HybridRetriever()
