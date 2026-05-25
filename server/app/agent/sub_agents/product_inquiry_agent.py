"""
Product Inquiry Agent — 针对已展示商品的追问。

用户已看到商品卡片，想了解某款商品的具体规格（颜色、尺码等），
或有功效、成分、FAQ 方面的问题。

流程：
  Step 1: 确定目标商品（从 params["product_id"] 或 last_shown[0] 兜底）
  Step 2: 组装商品详情上下文（SKU 规格聚合、营销文案、FAQ）
  Step 3: 流式生成回答（不推商品卡片）
"""
from typing import AsyncIterator

from app.agent.middleware import middleware
from app.db import relational as db
from app.db.product_repo import product_repo
from app.models import events as ev


class ProductInquiryAgent:

    async def run(
        self,
        session_id: str,
        message: str,
        params: dict,
        session: dict,
        image_base64: str | None = None,
    ) -> AsyncIterator[str]:

        # ── Step 1: 确定目标商品 ─────────────────────────────
        last_shown = session.get("last_shown_products", [])
        product_id = params.get("product_id")

        # 没有 product_id 时取 last_shown rank=1 的商品（用户指代"这款"最可能是最近推的第一个）
        if not product_id and last_shown:
            product_id = last_shown[0].get("product_id")

        if not product_id:
            yield ev.text_delta(
                "抱歉，我没能确定您在问哪款商品，请描述一下商品名称或重新搜索。"
            ).to_sse()
            return

        product = product_repo.get(product_id)
        if not product:
            yield ev.text_delta("抱歉，暂时无法获取该商品的详细信息。").to_sse()
            return

        # 记录本轮追问的商品，供后续"帮我加入购物车"时直接定位（避免错位到 last_shown[0]）
        order_info = dict(session.get("order_state") or {})
        order_info["last_inquired_product_id"] = product_id
        await db.update_order_state(session_id, order_info)

        # ── Step 2: 组装上下文 ──────────────────────────────
        context_parts = [
            f"商品名称：{product.title}",
            f"品牌：{product.brand} | 类目：{product.sub_category}",
        ]

        # SKU 规格汇总：把所有 SKU 的属性聚合成 key→唯一值列表
        if product.skus:
            prop_map: dict[str, list[str]] = {}
            for sku in product.skus:
                for key, val in sku.properties.items():
                    if key not in prop_map:
                        prop_map[key] = []
                    if val not in prop_map[key]:
                        prop_map[key].append(val)
            if prop_map:
                sku_lines = "\n".join(
                    f"  {k}：{'、'.join(vs)}" for k, vs in prop_map.items()
                )
                context_parts.append(f"可选规格：\n{sku_lines}")

        # 营销文案（最多 800 字）
        if product.rag_knowledge and product.rag_knowledge.marketing_description:
            mk = product.rag_knowledge.marketing_description
            context_parts.append(f"商品介绍：{mk[:800]}")

        # FAQ（最多 3 条）
        if product.rag_knowledge and product.rag_knowledge.official_faq:
            faq_lines = [
                f"  Q：{faq.question}\n  A：{faq.answer}"
                for faq in product.rag_knowledge.official_faq[:3]
            ]
            context_parts.append("常见问题：\n" + "\n".join(faq_lines))

        context = "\n\n".join(context_parts)

        # ── Step 3: 流式生成回答 ────────────────────────────
        history = session.get("recent_messages", [])
        user_messages = [
            {"role": m["role"], "content": m["content"]}
            for m in history[-4:]
        ]
        if not user_messages or user_messages[-1]["content"] != message:
            user_messages.append({"role": "user", "content": message})

        async for token in middleware.chat_stream(
            agent_name="product_inquiry",
            user_messages=user_messages,
            prompt_vars={"context": context},
            temperature=0.7,
        ):
            yield ev.text_delta(token).to_sse()

        # 引导用户做下一步决定
        # "推荐其他[sub_category]" 带上类目，检索器才能找到同类商品
        yield ev.clarification(
            question="需要帮您加入购物车吗？",
            options=["帮我加入购物车", f"推荐其他{product.sub_category}"],
        ).to_sse()


product_inquiry_agent = ProductInquiryAgent()
