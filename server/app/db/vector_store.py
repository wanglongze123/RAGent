"""
向量存储抽象层。
本地用 Chroma（VECTOR_STORE=chroma），生产用 Qdrant（VECTOR_STORE=qdrant）。
上层接口不变，切换只需改环境变量。
"""
from typing import Optional, Any
from abc import ABC, abstractmethod

from app.config import settings


class VectorStore(ABC):
    """向量存储统一接口 — Chroma / VikingDB 实现都遵循此接口"""

    @abstractmethod
    def add(
        self,
        ids: list[str],
        embeddings: list[list[float]],
        documents: list[str],
        metadatas: list[dict[str, Any]],
    ) -> None: ...

    @abstractmethod
    def query(
        self,
        query_embedding: list[float],
        top_k: int = 10,
        where: Optional[dict[str, Any]] = None,
    ) -> list[dict[str, Any]]: ...

    @abstractmethod
    def count(self) -> int: ...

    @abstractmethod
    def reset(self) -> None: ...


class ChromaVectorStore(VectorStore):
    """Chroma 实现 — 本地开发用，数据持久化到磁盘文件"""

    def __init__(self, collection_name: str = "products"):
        import chromadb
        from chromadb.config import Settings as ChromaSettings
        self._client = chromadb.PersistentClient(
            path=settings.chroma_persist_dir,
            settings=ChromaSettings(anonymized_telemetry=False),
        )
        self._collection = self._client.get_or_create_collection(
            name=collection_name,
            metadata={"hnsw:space": "cosine"},
        )

    def add(
        self,
        ids: list[str],
        embeddings: list[list[float]],
        documents: list[str],
        metadatas: list[dict[str, Any]],
    ) -> None:
        self._collection.upsert(
            ids=ids,
            embeddings=embeddings,
            documents=documents,
            metadatas=metadatas,
        )

    def query(
        self,
        query_embedding: list[float],
        top_k: int = 10,
        where: Optional[dict[str, Any]] = None,
    ) -> list[dict[str, Any]]:
        result = self._collection.query(
            query_embeddings=[query_embedding],
            n_results=top_k,
            where=where,
        )
        # Chroma 返回是 list of list（支持多个 query），我们只查一个所以取 [0]
        out = []
        if not result["ids"] or not result["ids"][0]:
            return out
        for i, _id in enumerate(result["ids"][0]):
            out.append({
                "id": _id,
                "document": result["documents"][0][i],
                "metadata": result["metadatas"][0][i],
                "distance": result["distances"][0][i],
                # cosine 距离转相似度：score = 1 - distance
                "score": 1.0 - result["distances"][0][i],
            })
        return out

    def count(self) -> int:
        return self._collection.count()

    def reset(self) -> None:
        """重建集合 — 全量重建索引时用"""
        name = self._collection.name
        self._client.delete_collection(name)
        self._collection = self._client.create_collection(
            name=name,
            metadata={"hnsw:space": "cosine"},
        )


class QdrantVectorStore(VectorStore):
    """Qdrant 实现 — 生产环境，独立服务，支持高并发"""

    def __init__(self, collection_name: str = "products"):
        from qdrant_client import QdrantClient
        from qdrant_client.models import Distance, VectorParams, OptimizersConfigDiff
        self._collection = collection_name
        self._client = QdrantClient(host=settings.qdrant_host, port=settings.qdrant_port)
        # 如果集合不存在则创建（向量维度 2048，豆包 embedding-vision 的维度）
        existing = [c.name for c in self._client.get_collections().collections]
        if collection_name not in existing:
            self._client.create_collection(
                collection_name=collection_name,
                vectors_config=VectorParams(size=2048, distance=Distance.COSINE),
                optimizers_config=OptimizersConfigDiff(memmap_threshold=20000),
            )

    def add(self, ids, embeddings, documents, metadatas) -> None:
        from qdrant_client.models import PointStruct
        points = [
            PointStruct(
                id=abs(hash(id_)) % (2**63),  # Qdrant 需要整数 ID
                vector=emb,
                payload={"_id": id_, "document": doc, **meta},
            )
            for id_, emb, doc, meta in zip(ids, embeddings, documents, metadatas)
        ]
        self._client.upsert(collection_name=self._collection, points=points)

    def query(self, query_embedding, top_k=10, where=None) -> list[dict]:
        from qdrant_client.models import Filter, FieldCondition, Range, MatchValue
        query_filter = None
        if where:
            query_filter = self._build_filter(where)
        results = self._client.search(
            collection_name=self._collection,
            query_vector=query_embedding,
            limit=top_k,
            query_filter=query_filter,
            with_payload=True,
        )
        out = []
        for r in results:
            payload = r.payload or {}
            out.append({
                "id": payload.get("_id", str(r.id)),
                "document": payload.get("document", ""),
                "metadata": {k: v for k, v in payload.items() if k not in ("_id", "document")},
                "score": r.score,
                "distance": 1.0 - r.score,
            })
        return out

    def _build_filter(self, where: dict):
        from qdrant_client.models import Filter, FieldCondition, Range, MatchValue, Must
        conditions = []
        for key, val in where.items():
            if key == "$and":
                for sub in val:
                    conditions.extend(self._build_filter(sub).must)
            elif isinstance(val, dict):
                field_conds = {}
                if "$gte" in val:
                    field_conds["gte"] = val["$gte"]
                if "$lte" in val:
                    field_conds["lte"] = val["$lte"]
                if "$lt" in val:
                    field_conds["lt"] = val["$lt"]
                if field_conds:
                    conditions.append(FieldCondition(key=key, range=Range(**field_conds)))
            else:
                conditions.append(FieldCondition(key=key, match=MatchValue(value=val)))
        return Filter(must=conditions) if conditions else None

    def count(self) -> int:
        return self._client.get_collection(self._collection).points_count

    def reset(self) -> None:
        from qdrant_client.models import Distance, VectorParams
        self._client.delete_collection(self._collection)
        self._client.create_collection(
            collection_name=self._collection,
            vectors_config=VectorParams(size=2048, distance=Distance.COSINE),
        )


def get_vector_store(collection_name: str = "products") -> VectorStore:
    """工厂方法 — 根据 VECTOR_STORE 配置返回对应实现"""
    if settings.vector_store == "qdrant":
        return QdrantVectorStore(collection_name)
    else:
        return ChromaVectorStore(collection_name)
