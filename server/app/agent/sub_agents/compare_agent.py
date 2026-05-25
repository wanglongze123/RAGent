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
import asyncio
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

        # ── Step 2: 直接从 product_repo 构建对比上下文（无需检索）──
        _CN = ["第一款", "第二款", "第三款", "第四款", "第五款"]
        context_blocks = []
        for i, product in enumerate(products):
            min_price = min((s.price for s in product.skus), default=product.base_price)
            props = list(product.skus[0].properties.items())[:3] if product.skus else []
            props_text = "\n".join(f"{k}  {v}" for k, v in props)
            desc = ""
            if product.rag_knowledge and product.rag_knowledge.marketing_description:
                desc = f"简介  {product.rag_knowledge.marketing_description[:150]}"
            block = (
                f"{_CN[i]}  {product.brand}  {product.display_title}\n"
                f"价格  ¥{min_price:.0f} 起\n"
                f"{props_text}\n"
                f"{desc}"
            ).strip()
            context_blocks.append(block)

        context = "\n\n".join(context_blocks)

        # ── Step 3: LLM 生成竖排对比文字（流式）────────────────
        async for token in middleware.chat_stream(
            agent_name="compare",
            user_messages=[{"role": "user", "content": message or "对比这几款商品"}],
            prompt_vars={"context": context},
            temperature=0.6,
        ):
            yield ev.text_delta(token).to_sse()

        # ── Step 4: 分款加购按钮 ─────────────────────────────
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

    async def _extract_dimensions(
        self,
        context_a: str,
        context_b: str,
        user_question: str,
    ) -> list[dict]:
        """
        调 LLM（JSON Mode）从两个商品的资料里提取对比维度。
        输出格式：[{"name": "价格", "values": ["¥720", "¥1690"]}, ...]
        """
        prompt_content = f"""从以下两个商品的资料中，提取 3-5 个对用户决策最有帮助的对比维度。

用户问题：{user_question}

【商品A资料】
{context_a[:600]}

【商品B资料】
{context_b[:600]}

输出 JSON，格式严格如下（不要任何额外字段）：
{{
  "dimensions": [
    {{"name": "维度名称", "values": ["商品A的值", "商品B的值"]}},
    ...
  ]
}}

规则：
- 只提取资料中明确有的信息
- 某商品某维度无数据时，values 里写"资料中未提及"
- 价格维度的 values 格式：["¥720", "¥1690"]
"""
        raw = await middleware.chat(
            agent_name="compare",
            user_messages=[{"role": "user", "content": prompt_content}],
            json_mode=True,
            temperature=0.0,
        )
        try:
            data = json.loads(raw)
            return data.get("dimensions", [])
        except Exception:
            # 解析失败时返回基础价格对比
            return [{"name": "价格", "values": [
                f"¥{product_repo.get(p['product_id']).base_price if product_repo.get(p['product_id']) else '?'}"
                for p in []
            ]}]


# ─────────────────────────────────────────────────────────
# 工具函数
# ─────────────────────────────────────────────────────────

def _build_product_context(product, results: list[dict]) -> str:
    """把检索结果拼成给 LLM 用的上下文"""
    if not results:
        return f"品牌: {product.brand} | 价格: ¥{product.base_price}"
    top = results[0]
    chunks = "\n".join(c.content for c in top["hit_chunks"][:3])
    return f"品牌: {product.brand} | 价格: ¥{product.base_price}\n{chunks}"


compare_agent = CompareAgent()
