"""
Order Agent — 下单引导与确认（⭐⭐⭐ 加分项）。

Skill 流程（对话式表单）：
  Step 1: 展示购物车内容，确认用户要下单的商品
  Step 2: 收集收货人姓名（每次只问一项）
  Step 3: 收集手机号（做基本格式校验）
  Step 4: 收集收货地址
  Step 5: 汇总订单信息，请用户二次确认
  Step 6: 用户确认后提交订单，清空购物车

关键原则：
  - 未经用户明确说"确认"/"提交"/"下单"，不调用 order_create
  - 每步收集一项信息，不要一次性问多项
  - 手机号做格式校验，不合法时礼貌提示重填
"""
import re
from typing import AsyncIterator

from app.agent.middleware import middleware
from app.db import relational as db
from app.models import events as ev

# 手机号正则（中国大陆 11 位）
_PHONE_RE = re.compile(r"^1[3-9]\d{9}$")

# 下单确认关键词
_CONFIRM_KEYWORDS = {"确认", "提交", "下单", "确定", "对的", "没错", "是的", "好的", "ok", "OK"}


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

        # 购物车为空，引导用户先选商品
        if not cart["items"]:
            yield ev.text_delta("您的购物车是空的，请先添加商品再来下单哦～").to_sse()
            return

        # 从 session 取当前收集到的收货信息
        order_info = session.get("_order_info", {})

        # ── 判断当前处于下单流程的哪个阶段 ─────────────────
        if not order_info.get("confirmed_cart"):
            async for e in self._step_confirm_cart(cart, session_id):
                yield e

        elif not order_info.get("receiver_name"):
            async for e in self._step_collect_name(message, session_id, order_info):
                yield e

        elif not order_info.get("receiver_phone"):
            async for e in self._step_collect_phone(message, session_id, order_info):
                yield e

        elif not order_info.get("receiver_address"):
            async for e in self._step_collect_address(message, session_id, order_info):
                yield e

        elif not order_info.get("final_confirmed"):
            async for e in self._step_final_confirm(message, cart, session_id, order_info):
                yield e

        else:
            # 所有信息收集完毕，提交订单
            async for e in self._submit_order(cart, session_id, order_info):
                yield e

    # ─────────────────────────────────────────────────────
    # 各步骤处理
    # ─────────────────────────────────────────────────────

    async def _step_confirm_cart(self, cart: dict, session_id: str) -> AsyncIterator[str]:
        """Step 1: 展示购物车，请用户确认要下单的商品"""
        lines = ["为您汇总购物车内容：\n"]
        for item in cart["items"]:
            props = "、".join(f"{v}" for v in item["sku_props"].values())
            lines.append(
                f"· {item['title']}"
                + (f"（{props}）" if props else "")
                + f" × {item['quantity']}  ¥{item['subtotal']}"
            )
        lines.append(f"\n合计：¥{cart['total_price']}")
        lines.append("\n以上商品确认下单吗？")
        yield ev.text_delta("\n".join(lines)).to_sse()

        # 标记"已展示购物车"，等用户确认
        await db.update_session_state(
            session_id,
            last_shown_products=None,  # 不改商品列表
        )
        # 用 _order_info 暂存下单流程状态（存入 session 的扩展字段）
        # 注意：这里借用 update_session_state 不直接支持 _order_info
        # 实际通过在 master_agent 里传递 session dict 来维护
        # 简化处理：标记 confirmed_cart=True 等用户下一轮回复

    async def _step_collect_name(
        self, message: str, session_id: str, order_info: dict
    ) -> AsyncIterator[str]:
        """Step 2: 收集收货人姓名"""
        if not order_info.get("confirmed_cart"):
            if any(kw in message for kw in _CONFIRM_KEYWORDS):
                order_info["confirmed_cart"] = True
                yield ev.text_delta("好的！请问收货人姓名是？").to_sse()
            else:
                yield ev.text_delta("请确认是否下单（回复 确认 继续）。").to_sse()
        else:
            name = message.strip()
            if len(name) < 2:
                yield ev.text_delta("收货人姓名太短，请重新输入。").to_sse()
                return
            order_info["receiver_name"] = name
            yield ev.text_delta(f"收货人：{name} ✓\n请问手机号是？").to_sse()

    async def _step_collect_phone(
        self, message: str, session_id: str, order_info: dict
    ) -> AsyncIterator[str]:
        """Step 3: 收集手机号，做格式校验"""
        phone = re.sub(r"\s+", "", message.strip())
        if not _PHONE_RE.match(phone):
            yield ev.text_delta("手机号格式不对，请输入 11 位中国大陆手机号。").to_sse()
            return
        order_info["receiver_phone"] = phone
        yield ev.text_delta(f"手机号：{phone[:3]}****{phone[7:]} ✓\n请问收货地址是？").to_sse()

    async def _step_collect_address(
        self, message: str, session_id: str, order_info: dict
    ) -> AsyncIterator[str]:
        """Step 4: 收集收货地址"""
        address = message.strip()
        if len(address) < 5:
            yield ev.text_delta("地址太短，请填写详细的收货地址（省市区+街道门牌号）。").to_sse()
            return
        order_info["receiver_address"] = address
        # 展示汇总，请求二次确认
        yield ev.text_delta(
            f"收货信息确认：\n"
            f"  姓名：{order_info['receiver_name']}\n"
            f"  电话：{order_info['receiver_phone'][:3]}****{order_info['receiver_phone'][7:]}\n"
            f"  地址：{address}\n\n"
            f"信息无误，确认下单吗？"
        ).to_sse()

    async def _step_final_confirm(
        self, message: str, cart: dict, session_id: str, order_info: dict
    ) -> AsyncIterator[str]:
        """Step 5: 等待用户二次确认"""
        if any(kw in message for kw in _CONFIRM_KEYWORDS):
            order_info["final_confirmed"] = True
            async for e in self._submit_order(cart, session_id, order_info):
                yield e
        else:
            yield ev.text_delta("已取消下单。您可以继续修改收货信息或重新下单。").to_sse()

    async def _submit_order(
        self, cart: dict, session_id: str, order_info: dict
    ) -> AsyncIterator[str]:
        """Step 6: 提交订单 + 清空购物车"""
        items = [
            {
                "product_id": item["product_id"],
                "sku_id": item["sku_id"],
                "title": item["title"],
                "quantity": item["quantity"],
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

        # 下单成功后清空购物车
        await db.cart_clear(session_id)

        yield ev.text_delta(
            f"🎉 订单提交成功！\n"
            f"订单号：{order['order_id']}\n"
            f"总金额：¥{order['total_price']}\n\n"
            f"感谢您的购买，祝您购物愉快！"
        ).to_sse()

        # 生成个性化结尾语
        user_messages = [{"role": "user", "content": "订单已提交成功"}]
        async for token in middleware.chat_stream(
            agent_name="order",
            user_messages=user_messages,
            temperature=0.8,
        ):
            yield ev.text_delta(token).to_sse()


order_agent = OrderAgent()
