"""
Order Agent — 下单引导与确认（⭐⭐⭐ 加分项）。

Skill 流程（对话式表单，每步状态持久化到 SQLite sessions.order_state）：
  Step 1: 展示购物车，问"确认下单吗"
  Step 2: 用户确认 → 收姓名
  Step 3: 收手机号（格式校验）
  Step 4: 收地址
  Step 5: 汇总信息，请二次确认
  Step 6: 用户确认 → 提交订单 → 清空购物车 → 清空 order_state

关键：每步把 order_state 写回数据库，保证多轮请求间状态连续。
"""
import re
from typing import AsyncIterator

from app.agent.middleware import middleware
from app.db import relational as db
from app.models import events as ev

# 中国大陆手机号
_PHONE_RE = re.compile(r"^1[3-9]\d{9}$")

_CONFIRM_KEYWORDS = {"确认", "提交", "下单", "确定", "对的", "没错", "是的", "好的", "ok", "OK"}
_CANCEL_KEYWORDS  = {"取消", "不要", "算了", "退出"}


class OrderAgent:

    async def run(
        self,
        session_id: str,
        message: str,
        params: dict,
        session: dict,
        image_base64: str | None = None,
    ) -> AsyncIterator[str]:

        cart = await db.cart_get(session_id)
        if not cart["items"]:
            # 购物车空就没有下单可言，退回 browsing 让用户继续逛
            await db.update_session_state(session_id, agent_state="browsing")
            await db.clear_order_state(session_id)
            yield ev.text_delta("您的购物车是空的，请先添加商品再来下单哦～").to_sse()
            return

        # 任何阶段说"算了/取消/不要了"都直接退出下单流程
        # 否则在收姓名阶段说"算了"会被当成 receiver_name="算了"
        if any(kw in message for kw in _CANCEL_KEYWORDS):
            async for e in self._cancel_order(session_id, session):
                yield e
            return

        # 从 session 读已持久化的下单流程状态
        order_info: dict = dict(session.get("order_state") or {})

        # ── 阶段判断（按字段是否填充判断当前步骤）─────────
        if not order_info.get("confirmed_cart"):
            async for e in self._handle_cart_confirm(message, cart, session_id, order_info):
                yield e

        elif not order_info.get("receiver_name"):
            # 若展示过历史地址选择框，先处理地址选择；否则收集姓名
            if order_info.get("_address_options") is not None:
                async for e in self._handle_address_select(message, session_id, order_info):
                    yield e
            else:
                async for e in self._handle_collect_name(message, session_id, order_info):
                    yield e

        elif not order_info.get("receiver_phone"):
            async for e in self._handle_collect_phone(message, session_id, order_info):
                yield e

        elif not order_info.get("receiver_address"):
            async for e in self._handle_collect_address(message, session_id, order_info):
                yield e

        elif not order_info.get("final_confirmed"):
            async for e in self._handle_final_confirm(message, cart, session_id, order_info):
                yield e

    # ─────────────────────────────────────────────────────
    # 各步骤处理（每步操作完都把 order_info 持久化）
    # ─────────────────────────────────────────────────────

    async def _handle_cart_confirm(
        self, message: str, cart: dict, session_id: str, order_info: dict
    ) -> AsyncIterator[str]:
        """
        进入下单流程时第一次调用：先展示购物车请用户确认。
        如果 order_info 是空的（首次进入），展示购物车。
        如果用户回复确认词，进入下一步收姓名。
        """
        # 用户已看过购物车，此次点击"确认下单" → 进入地址选择环节
        if order_info.get("_cart_shown") and any(kw in message for kw in _CONFIRM_KEYWORDS):
            order_info["confirmed_cart"] = True
            await db.update_order_state(session_id, order_info)

            saved = await db.get_used_addresses(session_id)
            if saved:
                options = [
                    f"{a['receiver_name']}  {a['receiver_phone'][:3]}****{a['receiver_phone'][7:]}  {a['receiver_address']}"
                    for a in saved
                ] + ["使用新地址"]
                order_info["_address_options"] = saved
                await db.update_order_state(session_id, order_info)
                yield ev.clarification(question="请选择收货地址：", options=options).to_sse()
            else:
                yield ev.text_delta("好的！请问收货人姓名是？").to_sse()
            return

        if any(kw in message for kw in _CANCEL_KEYWORDS):
            async for e in self._cancel_order(session_id, session):
                yield e
            return

        # 首次进入：展示购物车 + 确认/取消选择框
        lines = ["为您汇总购物车内容：\n"]
        for item in cart["items"]:
            props = "、".join(str(v) for v in item["sku_props"].values())
            lines.append(
                f"· {item['title']}"
                + (f"（{props}）" if props else "")
                + f" × {item['quantity']}  ¥{item['subtotal']}"
            )
        lines.append(f"\n合计：¥{cart['total_price']}")
        yield ev.clarification(
            question="\n".join(lines),
            options=["确认下单", "取消下单"],
        ).to_sse()
        order_info["_cart_shown"] = True
        await db.update_order_state(session_id, order_info)

    async def _handle_address_select(
        self, message: str, session_id: str, order_info: dict
    ) -> AsyncIterator[str]:
        """处理历史地址选择框的用户回复。"""
        saved: list[dict] = order_info.get("_address_options", [])

        if any(kw in message for kw in _CANCEL_KEYWORDS) or message.strip() == "使用新地址":
            order_info.pop("_address_options", None)
            await db.update_order_state(session_id, order_info)
            yield ev.text_delta("好的！请问收货人姓名是？").to_sse()
            return

        # 尝试匹配用户点击的地址选项
        selected = None
        for addr in saved:
            masked = f"{addr['receiver_phone'][:3]}****{addr['receiver_phone'][7:]}"
            option_text = f"{addr['receiver_name']}  {masked}  {addr['receiver_address']}"
            if message.strip() == option_text:
                selected = addr
                break

        if not selected:
            # 未匹配到（理论上不应发生），当作新地址处理
            order_info.pop("_address_options", None)
            await db.update_order_state(session_id, order_info)
            yield ev.text_delta("好的！请问收货人姓名是？").to_sse()
            return

        # 使用历史地址 → 直接跳到最终确认
        order_info["receiver_name"]    = selected["receiver_name"]
        order_info["receiver_phone"]   = selected["receiver_phone"]
        order_info["receiver_address"] = selected["receiver_address"]
        order_info.pop("_address_options", None)
        await db.update_order_state(session_id, order_info)

        masked = f"{selected['receiver_phone'][:3]}****{selected['receiver_phone'][7:]}"
        yield ev.clarification(
            question=(
                f"收货信息确认：\n"
                f"  姓名：{selected['receiver_name']}\n"
                f"  电话：{masked}\n"
                f"  地址：{selected['receiver_address']}"
            ),
            options=["确认提交订单", "取消下单"],
        ).to_sse()

    async def _handle_collect_name(
        self, message: str, session_id: str, order_info: dict
    ) -> AsyncIterator[str]:
        name = message.strip()
        if len(name) < 2 or len(name) > 20:
            yield ev.text_delta("姓名长度不合适，请重新输入收货人姓名。").to_sse()
            return
        order_info["receiver_name"] = name
        await db.update_order_state(session_id, order_info)
        yield ev.text_delta(f"收货人：{name} ✓\n请问联系手机号是？").to_sse()

    async def _handle_collect_phone(
        self, message: str, session_id: str, order_info: dict
    ) -> AsyncIterator[str]:
        phone = re.sub(r"\s+", "", message.strip())
        if not _PHONE_RE.match(phone):
            yield ev.text_delta("手机号格式不对，请输入 11 位中国大陆手机号。").to_sse()
            return
        order_info["receiver_phone"] = phone
        await db.update_order_state(session_id, order_info)
        masked = f"{phone[:3]}****{phone[7:]}"
        yield ev.text_delta(f"手机号：{masked} ✓\n请问收货地址是？（省市区+街道门牌号）").to_sse()

    async def _handle_collect_address(
        self, message: str, session_id: str, order_info: dict
    ) -> AsyncIterator[str]:
        address = message.strip()
        if len(address) < 5:
            yield ev.text_delta("地址过于简短，请填写完整收货地址（省市区+街道门牌号）。").to_sse()
            return
        order_info["receiver_address"] = address
        await db.update_order_state(session_id, order_info)

        phone = order_info["receiver_phone"]
        masked_phone = f"{phone[:3]}****{phone[7:]}"
        yield ev.clarification(
            question=(
                f"收货信息确认：\n"
                f"  姓名：{order_info['receiver_name']}\n"
                f"  电话：{masked_phone}\n"
                f"  地址：{address}"
            ),
            options=["确认提交订单", "取消下单"],
        ).to_sse()

    async def _handle_final_confirm(
        self, message: str, cart: dict, session_id: str, order_info: dict
    ) -> AsyncIterator[str]:
        if any(kw in message for kw in _CANCEL_KEYWORDS):
            async for e in self._cancel_order(session_id, session):
                yield e
            return

        if not any(kw in message for kw in _CONFIRM_KEYWORDS):
            yield ev.text_delta("请回复 确认 提交订单，或 取消 放弃下单。").to_sse()
            return

        # ── 提交订单 ─────────────────────────────────────
        items = [
            {
                "product_id": item["product_id"],
                "sku_id":     item["sku_id"],
                "title":      item["title"],
                "quantity":   item["quantity"],
                "unit_price": item["unit_price"],
            }
            for item in cart["items"]
        ]

        order = await db.order_create(
            session_id=session_id,
            receiver_name=order_info["receiver_name"],
            receiver_phone=order_info["receiver_phone"],
            receiver_address=order_info["receiver_address"],
            items=items,
            total_price=cart["total_price"],
        )

        # 下单成功 → 清空购物车 + 清空下单流程状态 + 退出 checkout 态 + 清空搜索上下文
        await db.cart_clear(session_id)
        await db.clear_order_state(session_id)
        await db.update_session_state(session_id, agent_state="browsing", last_shown_products=[])

        yield ev.text_delta(
            f"订单提交成功！\n"
            f"订单号：{order['order_id']}\n"
            f"总金额：¥{order['total_price']}\n"
            f"预计 3-5 个工作日内送达，感谢您的购买！"
        ).to_sse()

    async def _cancel_order(
        self, session_id: str, session: dict
    ) -> AsyncIterator[str]:
        """
        取消下单：清空下单状态，回到 browsing，
        并根据 last_shown_products 还原上一步的选择框。
        用户可以继续加购，或点「重新搜索」从头开始。
        """
        await db.clear_order_state(session_id)
        await db.update_session_state(session_id, agent_state="browsing")

        yield ev.text_delta("已取消下单，购物车保持不变。").to_sse()

        # 根据上次展示的商品恢复选项框（「加购第X款」+ 「重新搜索」）
        last_shown = session.get("last_shown_products", [])
        if last_shown:
            _CN = ["第一款", "第二款", "第三款", "第四款", "第五款"]
            options = [f"加购{_CN[i]}" for i in range(min(len(last_shown), 5))]
            options.append("细化需求")
            options.append("重新搜索")
            yield ev.clarification(
                question="您可以继续购物：",
                options=options,
            ).to_sse()
        else:
            yield ev.clarification(
                question="您可以继续购物：",
                options=["重新搜索"],
            ).to_sse()


order_agent = OrderAgent()
