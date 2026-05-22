"""
Reranker — Cross-Encoder 精排，把混合检索的候选集从 Top-15 精选到 Top-5。

为什么需要 Reranker：
  向量 + BM25 是"双塔模型"——query 和文档分别编码再比相似度，
  速度快但精度有限，因为编码时两者互不知情。
  Cross-Encoder 把 query + 文档拼在一起送进模型，
  能看到两者的完整交互，打分精度远高于双塔。
  代价是慢 100-1000 倍——所以只能用在候选集缩小后的精排阶段。

主路径：BGE Cross-Encoder（本地模型，快速精准）
降级路径：Doubao LLM 打分（API 调用，当本地模型不可用时兜底）
"""
import asyncio
from typing import Optional

from app.config import settings
from app.rag.retriever import RetrievedChunk


class BGEReranker:
    """本地 BGE Cross-Encoder Reranker（直接用 transformers 加载，兼容性更好）"""

    def __init__(self):
        self._tokenizer = None
        self._model = None
        self._available = False

    def load(self) -> bool:
        try:
            import torch
            from transformers import AutoModelForSequenceClassification, AutoTokenizer

            path = settings.reranker_model_path
            self._tokenizer = AutoTokenizer.from_pretrained(path, use_fast=False)
            self._model = AutoModelForSequenceClassification.from_pretrained(path)
            self._model.eval()
            self._torch = torch
            self._available = True
            print(f"[reranker] BGE 模型加载成功: {path}")
            return True
        except Exception as e:
            print(f"[reranker] BGE 模型加载失败，将使用 Doubao 降级: {e}")
            self._available = False
            return False

    def rerank(
        self,
        query: str,
        chunks: list[RetrievedChunk],
        top_k: int = 5,
    ) -> list[RetrievedChunk]:
        if not self._available or not chunks:
            return chunks[:top_k]

        pairs_a = [query] * len(chunks)
        pairs_b = [c.content[:400] for c in chunks]  # 截断避免超长

        with self._torch.no_grad():
            inputs = self._tokenizer(
                pairs_a, pairs_b,
                padding=True,
                truncation=True,
                max_length=512,
                return_tensors="pt",
            )
            logits = self._model(**inputs).logits.squeeze(-1)
            scores = logits.tolist()

        if isinstance(scores, float):  # 只有一个 chunk 时 squeeze 返回标量
            scores = [scores]

        scored = sorted(zip(chunks, scores), key=lambda x: x[1], reverse=True)
        reranked = []
        for chunk, score in scored[:top_k]:
            chunk.score = float(score)
            reranked.append(chunk)
        return reranked

    @property
    def available(self) -> bool:
        return self._available


class DoubaoReranker:
    """
    Doubao LLM 降级 Reranker。
    用大模型判断 (query, chunk) 相关性，输出 0-1 分数。
    比 BGE 慢，但不需要本地模型文件。
    """

    async def rerank(
        self,
        query: str,
        chunks: list[RetrievedChunk],
        top_k: int = 5,
    ) -> list[RetrievedChunk]:
        from app.llm.client import llm_client

        prompt_template = (
            "请判断以下商品信息与用户问题的相关程度，"
            "只输出一个 0 到 1 之间的小数，不要输出其他任何内容。\n"
            "用户问题：{query}\n"
            "商品信息：{doc}\n"
            "相关度分数："
        )

        async def score_one(chunk: RetrievedChunk) -> float:
            prompt = prompt_template.format(
                query=query,
                doc=chunk.content[:300],
            )
            try:
                result = await llm_client.chat(
                    messages=[{"role": "user", "content": prompt}],
                    temperature=0.0,
                )
                return float(result.strip())
            except Exception:
                return chunk.score

        scores = await asyncio.gather(*[score_one(c) for c in chunks])
        scored = sorted(zip(chunks, scores), key=lambda x: x[1], reverse=True)

        reranked = []
        for chunk, score in scored[:top_k]:
            chunk.score = float(score)
            reranked.append(chunk)
        return reranked


class Reranker:
    """
    统一 Reranker 入口 — 自动选择主路径或降级路径。
    主路径：BGE 本地模型（fast, free）
    降级路径：Doubao LLM（slow, costs tokens）
    """

    def __init__(self):
        self._bge = BGEReranker()
        self._doubao = DoubaoReranker()

    def load(self) -> str:
        if not settings.reranker_enabled:
            return "disabled"
        success = self._bge.load()
        return "bge" if success else "doubao"

    async def rerank(
        self,
        query: str,
        chunks: list[RetrievedChunk],
        top_k: int = 5,
    ) -> list[RetrievedChunk]:
        if not settings.reranker_enabled:
            return chunks[:top_k]

        if self._bge.available:
            return self._bge.rerank(query, chunks, top_k)
        else:
            return await self._doubao.rerank(query, chunks, top_k)


reranker = Reranker()
