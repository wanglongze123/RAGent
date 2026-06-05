"""
BM25 检索器 — 关键词精确召回，补向量检索的短板。

核心机制：
  TF-IDF 变体，关键在 IDF（逆文档频率）：
  - "洗面奶" 在 1092 个 chunk 里只在洁面商品出现 → IDF 高 → 主导打分
  - "护肤""适合""保湿" 到处都是 → IDF 低 → 影响小
  向量检索没有 IDF 概念，所有语义维度均匀对待，判别性词被稀释。
  两路互补：向量负责语义理解，BM25 负责关键词精确匹配。
"""
from typing import Any, Optional

import jieba
from rank_bm25 import BM25Okapi


def _tokenize(text: str) -> list[str]:
    """中文分词 — jieba 词级分词，比字符级更准确"""
    return list(jieba.cut(text))


def _matches_where(metadata: dict, where: dict) -> bool:
    """
    BM25 侧的 metadata 过滤（Chroma 有原生支持，BM25 需要手动实现）。
    支持 $and / $lt / $lte / $gt / $gte / $ne / 等值匹配。
    """
    if "$and" in where:
        return all(_matches_where(metadata, cond) for cond in where["$and"])
    if "$or" in where:
        return any(_matches_where(metadata, cond) for cond in where["$or"])

    for key, condition in where.items():
        if key.startswith("$"):
            continue
        val = metadata.get(key)
        if isinstance(condition, dict):
            for op, threshold in condition.items():
                if op == "$lt"  and not (val is not None and val < threshold):  return False
                if op == "$lte" and not (val is not None and val <= threshold): return False
                if op == "$gt"  and not (val is not None and val > threshold):  return False
                if op == "$gte" and not (val is not None and val >= threshold): return False
                if op == "$ne"  and not (val != threshold):                     return False
        else:
            if val != condition:
                return False
    return True


class BM25Retriever:
    """
    BM25 检索器，索引从 Chroma 加载，保证两路检索用的是同一份数据。
    """

    def __init__(self):
        self._ids: list[str] = []
        self._documents: list[str] = []
        self._metadatas: list[dict] = []
        self._bm25: Optional[BM25Okapi] = None
        self._tokenized_corpus: list[list[str]] = []
        self._built = False

    def build_from_records(
        self,
        ids: list[str],
        documents: list[str],
        metadatas: list[dict],
    ) -> int:
        """从全量记录构建 BM25 索引（与底层向量库无关）"""
        self._ids = ids
        self._documents = documents
        self._metadatas = metadatas

        # jieba 分词
        self._tokenized_corpus = [_tokenize(doc) for doc in self._documents]
        self._bm25 = BM25Okapi(self._tokenized_corpus)
        self._built = True
        return len(self._ids)

    def build_from_chroma(self, collection) -> int:
        """从 Chroma collection 加载全量数据并构建 BM25 索引（兼容旧调用）"""
        result = collection.get(include=["documents", "metadatas"])
        return self.build_from_records(
            result["ids"], result["documents"], result["metadatas"]
        )

    def search(
        self,
        query: str,
        top_k: int = 20,
        where: Optional[dict[str, Any]] = None,
    ) -> list[dict[str, Any]]:
        """
        BM25 检索。
        where 过滤在 BM25 打分后做（后过滤），
        返回格式与 VectorStore.query 一致，方便 RRF 融合。
        """
        if not self._built:
            raise RuntimeError("BM25 索引未构建，请先调用 build_from_chroma()")

        tokens = _tokenize(query)
        scores = self._bm25.get_scores(tokens)

        # 按分数降序排列所有 chunk
        ranked = sorted(
            zip(self._ids, self._documents, self._metadatas, scores),
            key=lambda x: x[3],
            reverse=True,
        )

        results = []
        for _id, doc, meta, score in ranked:
            if score <= 0:
                break  # BM25 分数为 0 的不相关，直接截断
            if where and not _matches_where(meta, where):
                continue
            results.append({
                "id": _id,
                "document": doc,
                "metadata": meta,
                "score": float(score),
            })
            if len(results) >= top_k:
                break

        return results


bm25_retriever = BM25Retriever()
