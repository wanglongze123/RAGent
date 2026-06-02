"""
Compare Agent — 多商品对比（⭐⭐⭐ 加分项）。

Skill 流程（四步法，借鉴 TikTok Shop 工作里的 Skill 编排思想）：
  Step 1: 商品识别 — 把用户说的商品名/指代解析成 product_id
  Step 2: 并行检索 — asyncio.gather 同时检索两个商品的 chunk
  Step 3: 维度提取 — LLM（JSON Mode）从 chunk 里抽取可对比属性
  Step 4: 表格组装 — 构造 comparison_table 事件（数据从 product_repo 取）
  Step 5: 推荐理由 — 流式生成"谁更适合谁"的文字

关键设计：对比表格的结构化数据（价格/标题/图片）全从 product_repo 取，
不让模型生成——模型只负责生成推荐理由文字。
"""
import json
from typing import AsyncIterator

from app.agent.middleware import middleware
from app.db.product_repo import product_repo
from app.models import events as ev
from app.rag.hybrid_retriever import hybrid_retriever


class CompareAgent:

    async def run(
        self,
        session_id: str,
        message: str,
        params: dict,
        session: dict,
        image_base64: str | None = None,
    ) -> AsyncIterator[str]:

        yield ev.tool_progress("compare", "正在生成对比分析...").to_sse()

        # ── Step 1: 商品识别（支持最多 5 款）──────────────────
        product_ids = await self._resolve_products(params, session, message)

        if len(product_ids) < 2:
            yield ev.text_delta(
                "请告诉我您想对比哪几款商品，例如：对比这几款面霜哪个更好。"
            ).to_sse()
            return

        products = [p for pid in product_ids if (p := product_repo.get(pid))]
        if len(products) < 2:
            yield ev.text_delta("抱歉，未能找到对应商品，请确认商品名称后重试。").to_sse()
            return

        # ── Step 2: 从 product_repo 构建结构化数据 + 给 LLM 的资料上下文 ──
        # 价格/标题/图片等硬数据全部来自 product_repo，绝不让模型生成。
        _CN = ["第一款", "第二款", "第三款", "第四款", "第五款"]
        table_products: list[dict] = []
        price_values: list[str] = []
        context_blocks: list[str] = []
        for i, product in enumerate(products):
            min_price = min((s.price for s in product.skus), default=product.base_price)
            table_products.append({
                "product_id": product.product_id,
                "title": product.display_title,
                "price": min_price,
                "image_url": product.image_url,
            })
            price_values.append(f"¥{min_price:.0f} 起")

            props = list(product.skus[0].properties.items())[:3] if product.skus else []
            props_text = "；".join(f"{k}:{v}" for k, v in props)
            desc = ""
            if product.rag_knowledge and product.rag_knowledge.marketing_description:
                desc = product.rag_knowledge.marketing_description[:300]
            context_blocks.append(
                f"【{_CN[i]}】{product.brand} {product.display_title}\n"
                f"规格：{props_text or '—'}\n"
                f"简介：{desc or '—'}"
            )
        context = "\n\n".join(context_blocks)

        # ── Step 3: LLM 结构化抽取对比维度（JSON Mode，值严格取自资料）──
        yield ev.tool_progress("compare_table", "正在分析各维度差异，生成对比表…").to_sse()
        dimensions, recommendation = await self._extract_table(
            n=len(products),
            context=context,
            question=message or "对比这几款商品",
            table_products=table_products,
        )
        # 价格维度由系统补，保证准确（不依赖 LLM）
        dimensions = [{"name": "价格", "values": price_values}] + dimensions

        # ── Step 4: 推送结构化对比表 ──────────────────────────
        yield ev.comparison_table(
            products=table_products,
            dimensions=dimensions,
            recommendation=recommendation,
        ).to_sse()

        # ── Step 5: 分款加购按钮 ─────────────────────────────
        buy_options = [f"加购{_CN[i]}" for i in range(len(products))]
        buy_options.append("重新搜索")
        yield ev.clarification(
            question="需要帮您加入购物车吗？",
            options=buy_options,
        ).to_sse()

    # ─────────────────────────────────────────────────────
    # 私有方法
    # ─────────────────────────────────────────────────────

    async def _resolve_products(
        self,
        params: dict,
        session: dict,
        message: str,
    ) -> list[str]:
        """
        把用户提到的商品名/指代解析成 product_id 列表。
        优先级：
          1. params 里已有 product_id（Master Agent 直接解析出来了）
          2. compare_products 里的商品名 → 搜索匹配
          3. last_shown_products 里取前两个（用户说"对比这两款"）
        """
        # 优先用 params 里的 product_id
        if params.get("product_id"):
            return [params["product_id"]]

        compare_names: list[str] = params.get("compare_products", [])
        last_shown: list[dict] = session.get("last_shown_products", [])

        product_ids: list[str] = []

        if compare_names:
            # 按名称搜索，取每个搜索的 Top1
            for name in compare_names[:2]:
                results = await hybrid_retriever.retrieve_products(
                    query=name, top_k_chunks=6, top_k_products=1
                )
                if results:
                    product_ids.append(results[0]["product_id"])
        elif last_shown:
            # 用最近展示的全部商品（最多5款）
            product_ids = [p["product_id"] for p in last_shown[:5]]

        return product_ids

    async def _extract_table(
        self,
        n: int,
        context: str,
        question: str,
        table_products: list[dict],
    ) -> tuple[list[dict], dict | None]:
        """
        JSON Mode 抽取对比维度 + 推荐。维度值严格来自资料（缺失填 "—"），
        解析/校验失败时降级为数据派生维度，保证对比表永远能展示、绝不编造。
        返回 (dimensions, recommendation|None)。
        """
        try:
            raw = await middleware.chat(
                agent_name="compare_table",
                user_messages=[{"role": "user", "content": question}],
                prompt_vars={"question": question, "context": context},
                json_mode=True,
                temperature=0.2,
            )
            data = json.loads(raw)
        except Exception:
            data = {}

        # 维度：只保留 values 长度与商品数对齐的，避免错位
        dimensions: list[dict] = []
        dims_in = data.get("dimensions") if isinstance(data, dict) else None
        if isinstance(dims_in, list):
            for d in dims_in:
                if not isinstance(d, dict):
                    continue
                name = str(d.get("name", "")).strip()
                vals = d.get("values")
                if name and isinstance(vals, list) and len(vals) == n:
                    dimensions.append({"name": name, "values": [str(v) for v in vals]})

        # 推荐：index 校验后映射成真实 product_id
        recommendation = None
        rec_in = data.get("recommendation") if isinstance(data, dict) else None
        if isinstance(rec_in, dict):
            idx = rec_in.get("index")
            reason = str(rec_in.get("reason", "")).strip()
            if isinstance(idx, int) and 1 <= idx <= n and reason:
                recommendation = {
                    "product_id": table_products[idx - 1]["product_id"],
                    "reason": reason,
                }

        # 兜底：LLM 没给出可用维度时，用数据派生的品牌维度，保证表非空
        if not dimensions:
            brands = []
            for p in table_products:
                prod = product_repo.get(p["product_id"])
                brands.append(prod.brand if prod else "—")
            dimensions = [{"name": "品牌", "values": brands}]

        return dimensions, recommendation


compare_agent = CompareAgent()
