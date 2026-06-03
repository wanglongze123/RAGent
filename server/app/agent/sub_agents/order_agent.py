"""
Order Agent — 下单引导与确认（⭐⭐⭐ 加分项）。

重构后的 4 步流程（原多轮逐字段收集 → 独立表单）：
  Step 1: 展示购物车，询问"确认下单吗"
  Step 2: 用户确认 → 发 order_form 事件，前端弹表单
  Step 3: 前端提交 ORDER_INFO:{...} → 解析三字段 → 二次确认
  Step 4: 用户二次确认 → 提交订单 → 清空购物车 + order_state

关键：每步把 order_state 写回数据库，保证多轮请求间状态连续。
"""
import json as _json
import re
from typing import AsyncIterator

from app.agent.middleware import middleware
from app.db import relational as db
from app.models import events as ev

# 中国大陆手机号
_PHONE_RE = re.compile(r"^1[3-9]\d{9}$")

_CONFIRM_KEYWORDS = {"确认", "提交", "下单", "确定", "对的", "没错", "是的", "好的", "ok", "OK"}
_CANCEL_KEYWORDS  = {"取消", "不要", "算了", "退出"}

# 前端表单提交的消息前缀
_ORDER_INFO_PREFIX = "ORDER_INFO:"


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
            await db.update_session_state(session_id, agent_state="browsing")
            await db.clear_order_state(session_id)
            yield ev.text_delta("您的购物车是空的，请先添加商品再来下单哦～").to_sse()
            return

        # 任何阶段说"算了/取消/不要了"都直接退出下单流程
        if any(kw in message for kw in _CANCEL_KEYWORDS):
            async for e in self._cancel_order(session_id, session):
                yield e
            return

        order_info: dict = dict(session.get("order_state") or {})

        # ── 阶段判断 ─────────────────────────────────────────
        if not order_info.get("confirmed_cart"):
            async for e in self._handle_cart_confirm(message, cart, session_id, order_info, session):
                yield e

        elif not order_info.get("form_submitted"):
            # confirmed_cart=True 但表单还没提交
            if message.startswith(_ORDER_INFO_PREFIX):
                # 前端已提交表单数据
                async for e in self._handle_form_reply(message, session_id, order_info):
                    yield e
            else:
                # 尚未收到表单数据（可能是第一次进入此步，也可能用户直接发文字）
                # 重新发 order_form 事件让前端弹表单
                async for e in self._handle_send_form(session_id):
                    yield e

        elif not order_info.get("final_confirmed"):
            async for e in self._handle_final_confirm(message, cart, session_id, order_info, session):
                yield e

    # ─────────────────────────────────────────────────────
    # 各步骤处理
    # ─────────────────────────────────────────────────────

    async def _handle_cart_confirm(
        self, message: str, cart: dict, session_id: str, order_info: dict, session: dict
    ) -> AsyncIterator[str]:
        """Step 1：展示购物车请用户确认。"""
        if order_info.get("_cart_shown") and any(kw in message for kw in _CONFIRM_KEYWORDS):
            order_info["confirmed_cart"] = True
            order_info.pop("_cart_shown", None)
            await db.update_order_state(session_id, order_info)
            # 直接进入 Step 2：发送 order_form 事件
            async for e in self._handle_send_form(session_id):
                yield e
            return

        # 首次展示购物车
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

    async def _handle_send_form(self, session_id: str) -> AsyncIterator[str]:
        """Step 2：发 order_form 事件，前端弹表单。附带历史地址供一键填入。"""
        saved = await db.get_used_addresses(session_id)
        yield ev.order_form(saved).to_sse()

    async def _handle_form_reply(
        self, message: str, session_id: str, order_info: dict
    ) -> AsyncIterator[str]:
        """Step 3：解析前端提交的 ORDER_INFO:{...}，校验三字段，通过则展示二次确认。"""
        raw = message[len(_ORDER_INFO_PREFIX):]
        try:
            data = _json.loads(raw)
        except Exception:
            # JSON 解析失败 → 重新弹表单
            async for e in self._handle_send_form(session_id):
                yield e
            return

        name    = str(data.get("name", "")).strip()
        phone   = re.sub(r"\s+", "", str(data.get("phone", "")).strip())
        address = str(data.get("address", "")).strip()

        errors = []
        if len(name) < 2 or len(name) > 20:
            errors.append("姓名长度需在 2~20 字之间")
        if not _PHONE_RE.match(phone):
            errors.append("手机号需为 11 位中国大陆号码")
        if len(address) < 5:
            errors.append("地址过于简短，请填写省市区+街道门牌号")

        if errors:
            yield ev.text_delta("填写信息有误：" + "；".join(errors) + "，请重新填写。").to_sse()
            async for e in self._handle_send_form(session_id):
                yield e
            return

        order_info["receiver_name"]    = name
        order_info["receiver_phone"]   = phone
        order_info["receiver_address"] = address
        order_info["form_submitted"]   = True
        await db.update_order_state(session_id, order_info)

        masked_phone = f"{phone[:3]}****{phone[7:]}"
        yield ev.clarification(
            question=(
                f"收货信息确认：\n"
                f"  姓名：{name}\n"
                f"  电话：{masked_phone}\n"
                f"  地址：{address}"
            ),
            options=["确认提交订单", "取消下单"],
        ).to_sse()

    async def _handle_final_confirm(
        self, message: str, cart: dict, session_id: str, order_info: dict, session: dict
    ) -> AsyncIterator[str]:
        """Step 4：二次确认后提交订单。"""
        if any(kw in message for kw in _CANCEL_KEYWORDS):
            async for e in self._cancel_order(session_id, session):
                yield e
            return

        if not any(kw in message for kw in _CONFIRM_KEYWORDS):
            yield ev.text_delta("请回复 确认 提交订单，或 取消 放弃下单。").to_sse()
            return

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

        await db.cart_clear(session_id)
        await db.clear_order_state(session_id)
        await db.update_session_state(session_id, agent_state="browsing", last_shown_products=[])

        yield ev.cart_update(
            action="checkout",
            product_id="",
            sku_id="",
            title="",
            quantity=0,
            cart_total_count=0,
            cart_total_price=0.0,
            message="",
        ).to_sse()

        yield ev.text_delta(
            f"订单提交成功！\n"
            f"订单号：{order['order_id']}\n"
            f"总金额：¥{order['total_price']}\n"
            f"预计 3-5 个工作日内送达，感谢您的购买！"
        ).to_sse()

    async def _cancel_order(
        self, session_id: str, session: dict
    ) -> AsyncIterator[str]:
        await db.clear_order_state(session_id)
        await db.update_session_state(session_id, agent_state="browsing")

        yield ev.text_delta("已取消下单，购物车保持不变。").to_sse()

        last_shown = session.get("last_shown_products", [])
        if last_shown:
            _CN = ["第一款", "第二款", "第三款", "第四款", "第五款"]
            options = [f"加购{_CN[i]}" for i in range(min(len(last_shown), 5))]
            options.append("细化需求")
            options.append("重新搜索")
            yield ev.clarification(question="您可以继续购物：", options=options).to_sse()
        else:
            yield ev.clarification(question="您可以继续购物：", options=["重新搜索"]).to_sse()


order_agent = OrderAgent()
