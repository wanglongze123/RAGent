"""
向量存储抽象层。
本地用 Chroma，生产切换到 VikingDB 只需替换实现，上层接口不变。
"""
from typing import Optional, Any
from abc import ABC, abstractmethod

import chromadb
from chromadb.config import Settings as ChromaSettings

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
        self._client = chromadb.PersistentClient(
            path=settings.chroma_persist_dir,
            settings=ChromaSettings(anonymized_telemetry=False),
        )
        self._collection = self._client.get_or_create_collection(
            name=collection_name,
            metadata={"hnsw:space": "cosine"},  # 余弦距离
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


def get_vector_store(collection_name: str = "products") -> VectorStore:
    """工厂方法 — 根据配置返回对应实现"""
    if settings.vector_store == "chroma":
        return ChromaVectorStore(collection_name)
    elif settings.vector_store == "vikingdb":
        # TODO: Phase 6（部署）实现 VikingDBVectorStore
        raise NotImplementedError("VikingDB 实现待补充")
    else:
        raise ValueError(f"未知的 vector_store: {settings.vector_store}")
