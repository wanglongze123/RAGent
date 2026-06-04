"""
豆包 API 客户端 — 对上层屏蔽具体模型实现。
- chat_stream: 流式文本生成
- chat:        非流式文本生成
- embed_text:  文本向量化
- embed_image: 图片向量化（Phase 5 用）
"""
from typing import AsyncIterator, Optional
import httpx
from openai import AsyncOpenAI
from openai.types.chat import ChatCompletionMessageParam

from app.config import settings


class DoubaoClient:
    def __init__(self):
        # 豆包 API 兼容 OpenAI 协议
        self._client = AsyncOpenAI(
            api_key=settings.doubao_api_key,
            base_url=settings.doubao_base_url,
            timeout=60.0,
        )
        # fast model 专用 client（可能用不同 API key）
        fast_key = settings.doubao_fast_api_key or settings.doubao_api_key
        self._fast_client = AsyncOpenAI(
            api_key=fast_key,
            base_url=settings.doubao_base_url,
            timeout=30.0,
        )

    async def chat_stream(
        self,
        messages: list[ChatCompletionMessageParam],
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
        response_format: Optional[dict] = None,
    ) -> AsyncIterator[str]:
        """流式生成 — 逐 token yield 文本"""
        stream = await self._client.chat.completions.create(
            model=settings.doubao_model,
            messages=messages,
            stream=True,
            temperature=temperature,
            max_tokens=max_tokens,
            response_format=response_format,
        )
        async for chunk in stream:
            if chunk.choices and chunk.choices[0].delta.content:
                yield chunk.choices[0].delta.content

    async def chat(
        self,
        messages: list[ChatCompletionMessageParam],
        temperature: float = 0.0,
        response_format: Optional[dict] = None,
    ) -> str:
        """非流式生成 — 用于意图分类等需要完整 JSON 输出的场景。
        Doubao-Seed-2.0-lite 不支持 response_format json_object，忽略该参数。
        """
        resp = await self._client.chat.completions.create(
            model=settings.doubao_model,
            messages=messages,
            stream=False,
            temperature=temperature,
        )
        return resp.choices[0].message.content or ""

    async def chat_fast(
        self,
        messages: list[ChatCompletionMessageParam],
        temperature: float = 0.0,
    ) -> str:
        """快速非流式生成 — 使用轻量模型，适合意图分类/judge 等结构化小任务。
        未配置 fast model 时自动回退到主模型。
        """
        if settings.doubao_fast_model:
            resp = await self._fast_client.chat.completions.create(
                model=settings.doubao_fast_model,
                messages=messages,
                stream=False,
                temperature=temperature,
            )
        else:
            resp = await self._client.chat.completions.create(
                model=settings.doubao_model,
                messages=messages,
                stream=False,
                temperature=temperature,
            )
        return resp.choices[0].message.content or ""

    async def embed_text(self, texts: list[str]) -> list[list[float]]:
        """
        文本向量化 — 批量。
        doubao-embedding-vision 使用 /embeddings/multimodal 接口，
        不支持标准 OpenAI /embeddings 接口，改用 httpx 直连。
        """
        url = f"{settings.doubao_base_url.rstrip('/')}/embeddings/multimodal"
        headers = {
            "Authorization": f"Bearer {settings.doubao_api_key}",
            "Content-Type": "application/json",
        }
        results = []
        for text in texts:
            payload = {
                "model": settings.doubao_embedding_model,
                "input": [{"type": "text", "text": text}],
            }
            async with httpx.AsyncClient(timeout=60.0) as client:
                resp = await client.post(url, headers=headers, json=payload)
                resp.raise_for_status()
                data = resp.json()
            results.append(data["data"]["embedding"])
        return results

    async def vlm_chat(
        self,
        prompt: str,
        image_base64: str,
        mime_type: str = "image/jpeg",
    ) -> str:
        """图+文字 → 文字。用于商品图类目识别 + OCR。"""
        resp = await self._client.chat.completions.create(
            model=settings.doubao_vision_model,
            messages=[{
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url",
                     "image_url": {"url": f"data:{mime_type};base64,{image_base64}"}},
                ],
            }],
            temperature=0.0,
        )
        return resp.choices[0].message.content or ""

    async def embed_image(self, image_base64: str, mime_type: str = "image/jpeg") -> list[float]:
        """
        图片向量化 — 使用 Doubao-embedding-vision 多模态接口。
        豆包多模态 embedding 不在 OpenAI SDK 标准接口里，用 httpx 直连。
        """
        url = f"{settings.doubao_base_url.rstrip('/')}/embeddings/multimodal"
        headers = {
            "Authorization": f"Bearer {settings.doubao_api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": settings.doubao_embedding_model,
            "input": [
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:{mime_type};base64,{image_base64}"},
                }
            ],
        }
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(url, headers=headers, json=payload)
            resp.raise_for_status()
            data = resp.json()
        return data["data"]["embedding"]


# 全局单例
llm_client = DoubaoClient()
