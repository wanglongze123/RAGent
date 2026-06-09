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
        elif action == "interpret":
            async for e in self._handle_interpret(session_id, message, params):
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
        order_info = dict(session.get("order_state") or {})

        # 如果上一轮已经问过规格，message 就是用户对规格的回答
        # 验证 pending 值有效（是真实 product_id），防止旧 session 的脏数据导致崩溃
        pending = order_info.get("pending_sku_product_id")
        if pending and not product_repo.get(pending):
            pending = None
            order_info.pop("pending_sku_product_id", None)
            from app.db.relational import update_order_state
            await update_order_state(session_id, order_info)
        if pending:
            product_id = pending
            params = dict(params)
            params["sku_selection_reply"] = message   # 传给 SKU 匹配逻辑
            # 清除 pending 状态
            order_info.pop("pending_sku_product_id", None)
            from app.db.relational import update_order_state
            await update_order_state(session_id, order_info)
        else:
            # 从 params → last_inquired_product_id → last_shown[0] 依次降级
            product_id = params.get("product_id")
            if not product_id:
                order_info = dict(session.get("order_state") or {})
                last_inquired = order_info.get("last_inquired_product_id")
                if last_inquired and product_repo.get(last_inquired):
                    product_id = last_inquired
                    # 用完即清，避免后续加购时错误复用
                    order_info.pop("last_inquired_product_id", None)
                    await db.update_order_state(session_id, order_info)
                else:
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
            # 用规格关键词匹配（如 "30ml" 匹配 "30ml 标准装"）
            reply = params.get("sku_selection_reply", "")
            if reply:
                for sku in product.skus:
                    props_str = "/".join(str(v) for v in sku.properties.values())
                    if any(kw in props_str for kw in reply.split()):
                        sku_id = sku.sku_id
                        break

        if not sku_id:
            if len(product.skus) == 1:
                sku_id = product.skus[0].sku_id
            else:
                # 多规格时询问用户，并把 product_id 存入 order_state
                options = [
                    f"{'/'.join(str(v) for v in sku.properties.values())} ¥{sku.price}"
                    for sku in product.skus
                ]
                order_info["pending_sku_product_id"] = product_id
                from app.db.relational import update_order_state
                await update_order_state(session_id, order_info)
                yield ev.clarification(
                    question="请问您需要哪个规格？",
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

        # cart_update 事件已携带 toast 提示，无需再调 LLM 生成确认文字
        # 直接给出下一步选择框，节省 3-8s 等待
        # 如果当前在场景购物流程，把剩余主题选项注入，让用户无需返回就能继续逛
        scene_ctx = session.get("scene_context") or {}
        sc_topics = scene_ctx.get("topics") or []
        remaining_topics = [f"了解{t['theme']}" for t in sc_topics if t.get("theme")]
        if remaining_topics:
            options = ["帮我下单", "查看购物车"] + remaining_topics + ["结束购物"]
        else:
            options = ["帮我下单", "查看购物车", f"推荐其他{product.sub_category}"]
        yield ev.clarification(
            question="接下来？",
            options=options,
        ).to_sse()

    async def _handle_remove(
        self,
        session_id: str,
        params: dict,
        message: str,
    ) -> AsyncIterator[str]:
        """删除购物车商品"""
        cart_item_id = params.get("cart_item_id")

        # LLM 通常只能给到 product_id（用户说"删掉理肤泉"），代码层把它解析成 cart_item_id
        if not cart_item_id:
            cart_item_id = await _resolve_cart_item_id(session_id, params, message)

        if not cart_item_id:
            # 还是没解析到 → 展示购物车让用户选
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

        action_summary = f"已从购物车移除商品。购物车现共 {cart['total_count']} 件，合计 ¥{cart['total_price']}。请简短确认删除成功。"
        async for token in middleware.chat_stream(
            agent_name="cart",
            user_messages=[{"role": "user", "content": action_summary}],
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

        if not cart_item_id:
            cart_item_id = await _resolve_cart_item_id(session_id, params, message)

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

        action_summary = (
            f"已将「{updated['title']}」数量修改为 {quantity} 件。"
            f"购物车现共 {cart['total_count']} 件，合计 ¥{cart['total_price']}。"
            f"请简短确认修改成功。"
        )
        async for token in middleware.chat_stream(
            agent_name="cart",
            user_messages=[{"role": "user", "content": action_summary}],
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
        yield ev.clarification(
            question="接下来？",
            options=["帮我下单"],
        ).to_sse()

    async def _handle_interpret(
        self,
        session_id: str,
        message: str,
        params: dict,
    ) -> AsyncIterator[str]:
        """
        cart_management 快速通道：跳过 master LLM，用专项 cart_interpret LLM
        一次调用完成"解析操作 + 生成回复"，比走 master LLM 快 3-5 倍。
        """
        import json as _json

        cart = await db.cart_get(session_id)
        items = cart.get("items", [])

        if not items:
            yield ev.text_delta("您的购物车是空的，快去挑选心仪的商品吧～").to_sse()
            return

        # 构建购物车上下文（含序号供 LLM 指代）
        lines = []
        for i, item in enumerate(items, 1):
            props = "/".join(str(v) for v in item["sku_props"].values()) if item["sku_props"] else ""
            lines.append(
                f"#{i} {item['title']}" + (f"（{props}）" if props else "")
                + f" × {item['quantity']} 件"
            )
        cart_context = "\n".join(lines)

        # 单次专项 LLM 调用：解析操作 + 生成回复
        raw = await middleware.chat(
            agent_name="cart_interpret",
            user_messages=[{"role": "user", "content": message}],
            prompt_vars={"cart_context": cart_context},
            json_mode=True,
            temperature=0.0,
        )

        try:
            result = _json.loads(raw)
        except Exception:
            yield ev.text_delta("抱歉没理解，请告诉我您想对购物车做什么？").to_sse()
            return

        action     = result.get("action", "unknown")
        item_index = int(result.get("item_index") or 0)
        quantity   = result.get("quantity")
        reply      = result.get("reply", "")

        # 校验序号，映射到实际购物车条目
        target = items[item_index - 1] if 1 <= item_index <= len(items) else None

        if action == "add" and target and quantity is not None:
            # 在现有数量上增加（"再来两件"）
            new_qty = target["quantity"] + int(quantity)
            updated = await db.cart_update_quantity(session_id, target["cart_item_id"], new_qty)
            if updated:
                updated_cart = await db.cart_get(session_id)
                yield ev.cart_update(
                    action="update_quantity",
                    product_id=updated["product_id"], sku_id=updated["sku_id"],
                    title=updated["title"], quantity=new_qty,
                    cart_total_count=updated_cart["total_count"],
                    cart_total_price=updated_cart["total_price"],
                    message=f"已将数量改为 {new_qty} 件",
                ).to_sse()

        elif action == "update_quantity" and target and quantity is not None:
            qty = int(quantity)
            if qty <= 0:
                success = await db.cart_remove(session_id, target["cart_item_id"])
                if success:
                    updated_cart = await db.cart_get(session_id)
                    yield ev.cart_update(
                        action="remove",
                        product_id=target["product_id"], sku_id=target["sku_id"],
                        title=target["title"], quantity=0,
                        cart_total_count=updated_cart["total_count"],
                        cart_total_price=updated_cart["total_price"],
                        message="商品已从购物车移除",
                    ).to_sse()
            else:
                updated = await db.cart_update_quantity(session_id, target["cart_item_id"], qty)
                if updated:
                    updated_cart = await db.cart_get(session_id)
                    yield ev.cart_update(
                        action="update_quantity",
                        product_id=updated["product_id"], sku_id=updated["sku_id"],
                        title=updated["title"], quantity=qty,
                        cart_total_count=updated_cart["total_count"],
                        cart_total_price=updated_cart["total_price"],
                        message=f"已将数量改为 {qty} 件",
                    ).to_sse()

        elif action == "remove" and target:
            success = await db.cart_remove(session_id, target["cart_item_id"])
            if success:
                updated_cart = await db.cart_get(session_id)
                yield ev.cart_update(
                    action="remove",
                    product_id=target["product_id"], sku_id=target["sku_id"],
                    title=target["title"], quantity=0,
                    cart_total_count=updated_cart["total_count"],
                    cart_total_price=updated_cart["total_price"],
                    message="已从购物车移除",
                ).to_sse()

        elif action == "view":
            async for e in self._handle_view(session_id):
                yield e
            return

        yield ev.text_delta(reply if reply else "操作已完成。").to_sse()

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


# ─────────────────────────────────────────────────────────
# 工具函数
# ─────────────────────────────────────────────────────────

# 中文位置词 → 排名（cart 内的 1-based 位置）
_CART_POSITION_WORDS: dict[str, int] = {
    "第一": 1, "第一个": 1, "第一款": 1, "第1个": 1, "第1款": 1,
    "第二": 2, "第二个": 2, "第二款": 2, "第2个": 2, "第2款": 2,
    "第三": 3, "第三个": 3, "第三款": 3, "第3个": 3, "第3款": 3,
    "第四": 4, "第四个": 4, "第四款": 4, "第4个": 4, "第4款": 4,
    "第五": 5, "第五个": 5, "第五款": 5, "第5个": 5, "第5款": 5,
}


async def _resolve_cart_item_id(
    session_id: str,
    params: dict,
    message: str,
) -> str | None:
    """
    把 product_id / 位置词 / 商品名 解析成 cart_item_id。
    适用于删除、改数量这种需要操作具体购物车条目的场景。

    优先级：
      1. params 里直接给了 product_id → 在 cart 里查同 product_id 的第一条
      2. 用户原话里的位置词（"第一个"）→ 取 cart.items[N-1]
      3. cart 只有一条 → 直接用唯一那一条
    """
    cart = await db.cart_get(session_id)
    items = cart.get("items", [])
    if not items:
        return None

    # 1. params.product_id → cart_item_id
    pid = params.get("product_id")
    if pid:
        for item in items:
            if item["product_id"] == pid:
                return item["cart_item_id"]

    # 2. 用户原话里的位置词
    for word, pos in _CART_POSITION_WORDS.items():
        if word in message and 1 <= pos <= len(items):
            return items[pos - 1]["cart_item_id"]

    # 3. cart 只有一条 → 唯一项
    if len(items) == 1:
        return items[0]["cart_item_id"]

    return None


cart_agent = CartAgent()
