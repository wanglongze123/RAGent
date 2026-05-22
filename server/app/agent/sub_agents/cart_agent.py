"""
Cart Agent — 购物车操作（⭐⭐ 加分项）。

Skill 流程：
  Step 1: 解析操作类型（add / remove / update_quantity / view）
  Step 2: 解析目标商品（从 params 或 last_shown_products 获取）
  Step 3: 如需选择 SKU，先询问用户规格
  Step 4: 执行数据库操作（cart_add / cart_remove / cart_update_quantity）
  Step 5: 推 cart_update 事件（客户端更新购物车角标）
  Step 6: 通过 middleware 生成自然语言确认回复

购物车操作是强结构化的，全部由代码执行，不让模型直接操作数据库。
模型只负责：解析用户意图 + 生成确认文字。
"""
from typing import AsyncIterator

from app.agent.middleware import middleware
from app.db import relational as db
from app.db.product_repo import product_repo
from app.models import events as ev


class CartAgent:

    async def run(
        self,
        session_id: str,
        message: str,
        params: dict,
        session: dict,
        image_base64: str | None = None,
    ) -> AsyncIterator[str]:

        action = params.get("cart_action", "add")

        if action == "view":
            async for e in self._handle_view(session_id):
                yield e
        elif action == "add":
            async for e in self._handle_add(session_id, params, session, message):
                yield e
        elif action == "remove":
            async for e in self._handle_remove(session_id, params, message):
                yield e
        elif action == "update_quantity":
            async for e in self._handle_update_quantity(session_id, params, message):
                yield e
        elif action == "clear":
            async for e in self._handle_clear(session_id):
                yield e
        else:
            yield ev.text_delta("请告诉我您想对购物车做什么操作？").to_sse()

    # ─────────────────────────────────────────────────────
    # 各操作处理
    # ─────────────────────────────────────────────────────

    async def _handle_add(
        self,
        session_id: str,
        params: dict,
        session: dict,
        message: str,
    ) -> AsyncIterator[str]:
        """加购处理"""
        # 从 params 或 last_shown_products 解析目标商品
        product_id = params.get("product_id")
        if not product_id:
            last_shown = session.get("last_shown_products", [])
            if last_shown:
                product_id = last_shown[0]["product_id"]

        if not product_id:
            yield ev.text_delta("请告诉我您想加购哪款商品？").to_sse()
            return

        product = product_repo.get(product_id)
        if not product:
            yield ev.text_delta("抱歉，未找到该商品，请重新搜索。").to_sse()
            return

        # 解析 SKU
        sku_id = params.get("sku_id")
        if not sku_id:
            if len(product.skus) == 1:
                sku_id = product.skus[0].sku_id
            else:
                # 多规格时询问用户
                options = [
                    f"{'/'.join(v for v in sku.properties.values())} ¥{sku.price}"
                    for sku in product.skus
                ]
                yield ev.clarification(
                    question=f"请问您需要哪个规格的 {product.title}？",
                    options=options,
                ).to_sse()
                return

        sku = next((s for s in product.skus if s.sku_id == sku_id), product.skus[0])
        quantity = int(params.get("quantity") or 1)

        # 执行加购（价格/标题快照存入数据库）
        cart_item = await db.cart_add(
            session_id=session_id,
            product_id=product_id,
            sku_id=sku.sku_id,
            title=product.title,
            image_url=product.image_url,
            sku_props=sku.properties,
            unit_price=sku.price,
            quantity=quantity,
        )

        cart = await db.cart_get(session_id)

        # 推 cart_update 事件（客户端更新购物车角标）
        yield ev.cart_update(
            action="add",
            product_id=product_id,
            sku_id=sku.sku_id,
            title=product.title,
            quantity=quantity,
            cart_total_count=cart["total_count"],
            cart_total_price=cart["total_price"],
            message=f"已将「{product.title}」加入购物车",
        ).to_sse()

        # 生成确认文字
        user_messages = [{"role": "user", "content": message}]
        async for token in middleware.chat_stream(
            agent_name="cart",
            user_messages=user_messages,
            temperature=0.3,
        ):
            yield ev.text_delta(token).to_sse()

    async def _handle_remove(
        self,
        session_id: str,
        params: dict,
        message: str,
    ) -> AsyncIterator[str]:
        """删除购物车商品"""
        cart_item_id = params.get("cart_item_id")
        if not cart_item_id:
            # 没有 cart_item_id 时展示购物车让用户选
            cart = await db.cart_get(session_id)
            if not cart["items"]:
                yield ev.text_delta("您的购物车是空的。").to_sse()
                return
            items_str = "\n".join(
                f"{i+1}. {item['title']} x{item['quantity']}"
                for i, item in enumerate(cart["items"])
            )
            yield ev.text_delta(f"您的购物车中有：\n{items_str}\n\n请告诉我您要删除哪一个？").to_sse()
            return

        success = await db.cart_remove(session_id, cart_item_id)
        if not success:
            yield ev.text_delta("未找到该商品，可能已从购物车移除。").to_sse()
            return

        cart = await db.cart_get(session_id)
        yield ev.cart_update(
            action="remove",
            product_id=params.get("product_id", ""),
            sku_id="",
            title="",
            quantity=0,
            cart_total_count=cart["total_count"],
            cart_total_price=cart["total_price"],
            message="已从购物车移除",
        ).to_sse()

        user_messages = [{"role": "user", "content": message}]
        async for token in middleware.chat_stream(
            agent_name="cart",
            user_messages=user_messages,
            temperature=0.3,
        ):
            yield ev.text_delta(token).to_sse()

    async def _handle_update_quantity(
        self,
        session_id: str,
        params: dict,
        message: str,
    ) -> AsyncIterator[str]:
        """修改购物车商品数量"""
        cart_item_id = params.get("cart_item_id")
        quantity = params.get("quantity")

        if not cart_item_id or not quantity:
            yield ev.text_delta("请告诉我您想修改哪个商品的数量以及改成几个？").to_sse()
            return

        updated = await db.cart_update_quantity(session_id, cart_item_id, int(quantity))
        if not updated:
            yield ev.text_delta("未找到该购物车商品，请重新查看购物车。").to_sse()
            return

        cart = await db.cart_get(session_id)
        yield ev.cart_update(
            action="update_quantity",
            product_id=updated["product_id"],
            sku_id=updated["sku_id"],
            title=updated["title"],
            quantity=updated["quantity"],
            cart_total_count=cart["total_count"],
            cart_total_price=cart["total_price"],
            message=f"已将数量修改为 {quantity} 个",
        ).to_sse()

        user_messages = [{"role": "user", "content": message}]
        async for token in middleware.chat_stream(
            agent_name="cart",
            user_messages=user_messages,
            temperature=0.3,
        ):
            yield ev.text_delta(token).to_sse()

    async def _handle_view(self, session_id: str) -> AsyncIterator[str]:
        """查看购物车"""
        cart = await db.cart_get(session_id)
        if not cart["items"]:
            yield ev.text_delta("您的购物车还是空的，快去挑选心仪的商品吧～").to_sse()
            return

        lines = [f"您的购物车共 {cart['total_count']} 件商品：\n"]
        for i, item in enumerate(cart["items"], 1):
            props = "、".join(f"{k}:{v}" for k, v in item["sku_props"].items())
            lines.append(
                f"{i}. {item['title']}"
                + (f"（{props}）" if props else "")
                + f" × {item['quantity']}  小计 ¥{item['subtotal']}"
            )
        lines.append(f"\n合计：¥{cart['total_price']}")
        yield ev.text_delta("\n".join(lines)).to_sse()

    async def _handle_clear(self, session_id: str) -> AsyncIterator[str]:
        """清空购物车"""
        await db.cart_clear(session_id)
        yield ev.cart_update(
            action="remove",
            product_id="",
            sku_id="",
            title="",
            quantity=0,
            cart_total_count=0,
            cart_total_price=0.0,
            message="购物车已清空",
        ).to_sse()
        yield ev.text_delta("购物车已清空。").to_sse()


cart_agent = CartAgent()
