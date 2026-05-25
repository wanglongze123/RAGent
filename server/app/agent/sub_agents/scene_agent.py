"""
Scene Agent — 场景化组合推荐（⭐⭐⭐ 加分项）。

Skill 流程：
  Step 1: LLM 把场景拆解为 2-4 个购物主题（JSON Mode）
  Step 2: asyncio.gather 并行检索所有主题
  Step 3: 推 product_card 事件（按主题顺序展示）
  Step 4: 流式生成场景化搭配推荐文案

适用消息：
  "下周去三亚度假，搭配防晒+穿搭整套方案"
  "夏天露营要带啥？"
  "给我妈准备生日礼物"

为什么不复用 search_agent：
  search_agent 是单 query 单类目，scene_agent 是多 query 跨类目。
  把场景规划的复杂度封装在独立 agent 里，search/compare/cart 都不用关心。
"""
import asyncio
import json
from typing import AsyncIterator

from app.agent.middleware import middleware
from app.db.product_repo import product_repo
from app.models import events as ev
from app.rag.hybrid_retriever import hybrid_retriever


class SceneAgent:

    async def run(
        self,
        session_id: str,
        message: str,
        params: dict,
        session: dict,
        image_base64: str | None = None,
    ) -> AsyncIterator[str]:

        # ── Step 1: 告知用户在规划 ─────────────────────────
        yield ev.tool_progress("scene_plan", "正在为您规划方案…").to_sse()

        # ── Step 2: LLM 拆解场景 ───────────────────────────
        plan = await self._plan_scene(message)
        if not plan or not plan.get("topics"):
            # 拆解失败时降级走 search agent — 把整条消息当 query
            yield ev.text_delta(
                "暂时没能识别您的场景需求，让我按整体推荐来找几款～\n"
            ).to_sse()
            return

        scene_summary = plan.get("scene_summary", "")
        topics: list[dict] = plan["topics"][:4]  # 最多 4 个主题，防止商品太多

        yield ev.tool_progress(
            "scene_plan",
            f"已拆解为 {len(topics)} 个主题：" + " / ".join(t.get("theme", "") for t in topics),
        ).to_sse()

        # ── Step 3: 并行检索所有主题 ──────────────────────
        retrieve_tasks = [
            hybrid_retriever.retrieve_products(
                query=t["query"],
                top_k_chunks=max(int(t.get("count", 1)), 1) * 6,
                top_k_products=max(int(t.get("count", 1)), 1),
            )
            for t in topics
        ]
        results: list[list[dict]] = await asyncio.gather(*retrieve_tasks)

        # ── Step 4: 推 product_card + 整理 LLM 上下文 ──────
        # 不同主题间可能召回同一商品（小数据集尤其常见），按 product_id 去重，
        # 同一商品只 yield 一次，归到首次出现的主题下。
        topics_with_products: list[str] = []
        seen_product_ids: set[str] = set()
        any_yielded = False
        for topic, hits in zip(topics, results):
            theme_name = topic.get("theme", "")
            theme_lines = [f"主题：{theme_name}"]
            theme_has_item = False
            for rp in hits:
                pid = rp["product_id"]
                if pid in seen_product_ids:
                    continue
                product = product_repo.get(pid)
                if not product:
                    continue
                seen_product_ids.add(pid)
                yield ev.product_card(
                    product_id=product.product_id,
                    title=product.display_title,
                    brand=product.brand,
                    image_url=product.image_url,
                    price=product.base_price,
                    sub_category=product.sub_category,
                ).to_sse()
                any_yielded = True
                theme_has_item = True

                chunk_texts = "\n".join(c.content for c in rp["hit_chunks"][:2])
                theme_lines.append(
                    f"  - 【{product.title}】{product.brand} | ¥{product.base_price}\n    {chunk_texts}"
                )
            if theme_has_item:
                topics_with_products.append("\n".join(theme_lines))

        if not any_yielded:
            yield ev.text_delta(
                "抱歉，按当前主题没找到合适的商品，您可以换个表达再试试～"
            ).to_sse()
            return

        # ── Step 5: 流式生成场景搭配推荐 ────────────────────
        async for token in middleware.chat_stream(
            agent_name="scene",
            user_messages=[{"role": "user", "content": message}],
            prompt_vars={
                "scene_summary": scene_summary,
                "topics_with_products": "\n\n".join(topics_with_products),
            },
            temperature=0.7,
        ):
            yield ev.text_delta(token).to_sse()

    # ─────────────────────────────────────────────────────
    # 私有方法
    # ─────────────────────────────────────────────────────

    async def _plan_scene(self, message: str) -> dict | None:
        """LLM 拆解场景为主题列表，返回解析后字典或 None"""
        raw = await middleware.chat(
            agent_name="scene_planning",
            user_messages=[{"role": "user", "content": message}],
            json_mode=True,
            temperature=0.3,
        )
        try:
            data = json.loads(raw)
            if isinstance(data, dict) and isinstance(data.get("topics"), list):
                return data
        except json.JSONDecodeError:
            pass
        return None


scene_agent = SceneAgent()
