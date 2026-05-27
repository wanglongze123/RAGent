"""
关系型数据层 — SQLite（本地开发），生产换 RDS MySQL 只改连接串。
负责：会话状态、对话历史、购物车、订单 的持久化。

设计原则：
  - 价格/标题在加购时快照到 cart_items，防止商品改价后购物车展示出错
  - 所有写操作立即 commit，不做事务批处理（简单场景够用）
  - 对外只暴露业务方法，上层不写任何 SQL
"""
import json
import uuid
from datetime import datetime
from typing import Optional

import aiosqlite

from app.config import settings


# ───────────────────────── 初始化 ─────────────────────────

CREATE_TABLES_SQL = """
CREATE TABLE IF NOT EXISTS sessions (
    session_id          TEXT PRIMARY KEY,
    agent_state         TEXT NOT NULL DEFAULT 'browsing',
    last_shown_products TEXT NOT NULL DEFAULT '[]',
    order_state         TEXT NOT NULL DEFAULT '{}',
    scene_context       TEXT NOT NULL DEFAULT '{}',
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS messages (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id  TEXT NOT NULL,
    role        TEXT NOT NULL,
    content     TEXT NOT NULL,
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS cart_items (
    cart_item_id TEXT PRIMARY KEY,
    session_id   TEXT NOT NULL,
    product_id   TEXT NOT NULL,
    sku_id       TEXT NOT NULL,
    title        TEXT NOT NULL,
    image_url    TEXT NOT NULL,
    sku_props    TEXT NOT NULL DEFAULT '{}',
    unit_price   REAL NOT NULL,
    quantity     INTEGER NOT NULL DEFAULT 1,
    created_at   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS orders (
    order_id         TEXT PRIMARY KEY,
    session_id       TEXT NOT NULL,
    status           TEXT NOT NULL DEFAULT 'confirmed',
    receiver_name    TEXT NOT NULL,
    receiver_phone   TEXT NOT NULL,
    receiver_address TEXT NOT NULL,
    total_price      REAL NOT NULL,
    created_at       TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS order_items (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id     TEXT NOT NULL,
    product_id   TEXT NOT NULL,
    sku_id       TEXT NOT NULL,
    title        TEXT NOT NULL,
    quantity     INTEGER NOT NULL,
    unit_price   REAL NOT NULL
);
"""


async def init_db() -> None:
    """应用启动时调用，创建所有表 + 处理增量迁移"""
    async with aiosqlite.connect(settings.sqlite_db_path) as db:
        await db.executescript(CREATE_TABLES_SQL)

        # 迁移：已存在的 sessions 表补上新字段
        cursor = await db.execute("PRAGMA table_info(sessions)")
        columns = [row[1] for row in await cursor.fetchall()]
        if "order_state" not in columns:
            await db.execute(
                "ALTER TABLE sessions ADD COLUMN order_state TEXT NOT NULL DEFAULT '{}'"
            )
        if "scene_context" not in columns:
            await db.execute(
                "ALTER TABLE sessions ADD COLUMN scene_context TEXT NOT NULL DEFAULT '{}'"
            )

        await db.commit()


# ───────────────────────── 会话 ─────────────────────────

async def create_session(session_id: str) -> dict:
    now = datetime.utcnow().isoformat()
    async with aiosqlite.connect(settings.sqlite_db_path) as db:
        await db.execute(
            "INSERT INTO sessions (session_id, agent_state, last_shown_products, created_at, updated_at) "
            "VALUES (?, 'browsing', '[]', ?, ?)",
            (session_id, now, now),
        )
        await db.commit()
    return {"session_id": session_id, "agent_state": "browsing", "created_at": now}


async def get_session(session_id: str) -> Optional[dict]:
    async with aiosqlite.connect(settings.sqlite_db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM sessions WHERE session_id = ?", (session_id,)
        ) as cursor:
            row = await cursor.fetchone()
    if not row:
        return None
    d = dict(row)
    d["last_shown_products"] = json.loads(d["last_shown_products"])
    d["order_state"] = json.loads(d.get("order_state") or "{}")
    d["scene_context"] = json.loads(d.get("scene_context") or "{}")
    return d


async def update_order_state(session_id: str, order_state: dict) -> None:
    """持久化下单流程的临时状态（收货人姓名、电话等）"""
    async with aiosqlite.connect(settings.sqlite_db_path) as db:
        await db.execute(
            "UPDATE sessions SET order_state = ?, updated_at = ? WHERE session_id = ?",
            (
                json.dumps(order_state, ensure_ascii=False),
                datetime.utcnow().isoformat(),
                session_id,
            ),
        )
        await db.commit()


async def clear_order_state(session_id: str) -> None:
    await update_order_state(session_id, {})


async def save_scene_context(session_id: str, scene_context: dict) -> None:
    """持久化场景化购物上下文（主题 + 检索 query），下单完成后保留，由用户手动结束/重规划时清空"""
    async with aiosqlite.connect(settings.sqlite_db_path) as db:
        await db.execute(
            "UPDATE sessions SET scene_context = ?, updated_at = ? WHERE session_id = ?",
            (
                json.dumps(scene_context, ensure_ascii=False),
                datetime.utcnow().isoformat(),
                session_id,
            ),
        )
        await db.commit()


async def clear_scene_context(session_id: str) -> None:
    await save_scene_context(session_id, {})


async def update_session_state(
    session_id: str,
    agent_state: Optional[str] = None,
    last_shown_products: Optional[list] = None,
) -> None:
    """更新会话的导购阶段状态和最近展示商品"""
    fields, values = [], []
    if agent_state is not None:
        fields.append("agent_state = ?")
        values.append(agent_state)
    if last_shown_products is not None:
        fields.append("last_shown_products = ?")
        values.append(json.dumps(last_shown_products, ensure_ascii=False))
    if not fields:
        return
    fields.append("updated_at = ?")
    values.append(datetime.utcnow().isoformat())
    values.append(session_id)
    sql = f"UPDATE sessions SET {', '.join(fields)} WHERE session_id = ?"
    async with aiosqlite.connect(settings.sqlite_db_path) as db:
        await db.execute(sql, values)
        await db.commit()


# ───────────────────────── 对话历史 ─────────────────────────

async def add_message(session_id: str, role: str, content: str) -> None:
    now = datetime.utcnow().isoformat()
    async with aiosqlite.connect(settings.sqlite_db_path) as db:
        await db.execute(
            "INSERT INTO messages (session_id, role, content, created_at) VALUES (?, ?, ?, ?)",
            (session_id, role, content, now),
        )
        await db.commit()


async def get_recent_messages(session_id: str, limit: int = 10) -> list[dict]:
    """取最近 N 条消息，用于多轮对话上下文"""
    async with aiosqlite.connect(settings.sqlite_db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT role, content, created_at FROM messages "
            "WHERE session_id = ? ORDER BY id DESC LIMIT ?",
            (session_id, limit),
        ) as cursor:
            rows = await cursor.fetchall()
    return [dict(r) for r in reversed(rows)]  # 时间正序


async def get_all_messages(session_id: str) -> list[dict]:
    async with aiosqlite.connect(settings.sqlite_db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT role, content, created_at FROM messages "
            "WHERE session_id = ? ORDER BY id ASC",
            (session_id,),
        ) as cursor:
            rows = await cursor.fetchall()
    return [dict(r) for r in rows]


# ───────────────────────── 购物车 ─────────────────────────

async def cart_add(
    session_id: str,
    product_id: str,
    sku_id: str,
    title: str,
    image_url: str,
    sku_props: dict,
    unit_price: float,
    quantity: int = 1,
) -> dict:
    """
    加购。同一 session + sku 已存在则累加数量。
    返回新增或更新的 cart_item。
    """
    async with aiosqlite.connect(settings.sqlite_db_path) as db:
        db.row_factory = aiosqlite.Row
        # 检查是否已有同款
        async with db.execute(
            "SELECT cart_item_id, quantity FROM cart_items "
            "WHERE session_id = ? AND sku_id = ?",
            (session_id, sku_id),
        ) as cursor:
            existing = await cursor.fetchone()

        if existing:
            new_qty = existing["quantity"] + quantity
            await db.execute(
                "UPDATE cart_items SET quantity = ? WHERE cart_item_id = ?",
                (new_qty, existing["cart_item_id"]),
            )
            await db.commit()
            cart_item_id = existing["cart_item_id"]
            quantity = new_qty
        else:
            cart_item_id = f"ci_{uuid.uuid4().hex[:12]}"
            now = datetime.utcnow().isoformat()
            await db.execute(
                "INSERT INTO cart_items "
                "(cart_item_id, session_id, product_id, sku_id, title, image_url, sku_props, unit_price, quantity, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (cart_item_id, session_id, product_id, sku_id, title, image_url,
                 json.dumps(sku_props, ensure_ascii=False), unit_price, quantity, now),
            )
            await db.commit()

    return {
        "cart_item_id": cart_item_id,
        "product_id": product_id,
        "sku_id": sku_id,
        "title": title,
        "image_url": image_url,
        "sku_props": sku_props,
        "unit_price": unit_price,
        "quantity": quantity,
    }


async def cart_update_quantity(
    session_id: str,
    cart_item_id: str,
    quantity: int,
) -> Optional[dict]:
    async with aiosqlite.connect(settings.sqlite_db_path) as db:
        await db.execute(
            "UPDATE cart_items SET quantity = ? "
            "WHERE cart_item_id = ? AND session_id = ?",
            (quantity, cart_item_id, session_id),
        )
        await db.commit()
    return await _get_cart_item(cart_item_id)


async def cart_remove(session_id: str, cart_item_id: str) -> bool:
    async with aiosqlite.connect(settings.sqlite_db_path) as db:
        cursor = await db.execute(
            "DELETE FROM cart_items WHERE cart_item_id = ? AND session_id = ?",
            (cart_item_id, session_id),
        )
        await db.commit()
    return cursor.rowcount > 0


async def cart_clear(session_id: str) -> None:
    async with aiosqlite.connect(settings.sqlite_db_path) as db:
        await db.execute("DELETE FROM cart_items WHERE session_id = ?", (session_id,))
        await db.commit()


async def cart_get(session_id: str) -> dict:
    async with aiosqlite.connect(settings.sqlite_db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM cart_items WHERE session_id = ? ORDER BY created_at ASC",
            (session_id,),
        ) as cursor:
            rows = await cursor.fetchall()

    items = []
    for row in rows:
        d = dict(row)
        d["sku_props"] = json.loads(d["sku_props"])
        d["subtotal"] = round(d["unit_price"] * d["quantity"], 2)
        items.append(d)

    total_count = sum(i["quantity"] for i in items)
    total_price = round(sum(i["subtotal"] for i in items), 2)
    return {"session_id": session_id, "items": items, "total_count": total_count, "total_price": total_price}


async def _get_cart_item(cart_item_id: str) -> Optional[dict]:
    async with aiosqlite.connect(settings.sqlite_db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM cart_items WHERE cart_item_id = ?", (cart_item_id,)
        ) as cursor:
            row = await cursor.fetchone()
    if not row:
        return None
    d = dict(row)
    d["sku_props"] = json.loads(d["sku_props"])
    d["subtotal"] = round(d["unit_price"] * d["quantity"], 2)
    return d


# ───────────────────────── 订单 ─────────────────────────

async def order_create(
    session_id: str,
    receiver_name: str,
    receiver_phone: str,
    receiver_address: str,
    items: list[dict],
    total_price: float,
) -> dict:
    order_id = f"ord_{datetime.utcnow().strftime('%Y%m%d%H%M%S')}_{uuid.uuid4().hex[:6]}"
    now = datetime.utcnow().isoformat()

    async with aiosqlite.connect(settings.sqlite_db_path) as db:
        await db.execute(
            "INSERT INTO orders "
            "(order_id, session_id, status, receiver_name, receiver_phone, receiver_address, total_price, created_at) "
            "VALUES (?, ?, 'confirmed', ?, ?, ?, ?, ?)",
            (order_id, session_id, receiver_name, receiver_phone, receiver_address, total_price, now),
        )
        for item in items:
            await db.execute(
                "INSERT INTO order_items (order_id, product_id, sku_id, title, quantity, unit_price) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (order_id, item["product_id"], item["sku_id"],
                 item.get("title", ""), item["quantity"], item["unit_price"]),
            )
        await db.commit()

    return {
        "order_id": order_id,
        "status": "confirmed",
        "message": "订单提交成功",
        "total_price": total_price,
        "created_at": now,
    }


async def get_used_addresses(session_id: str) -> list[dict]:
    """从本会话历史订单中提取去重地址（最多3条），供下单时快速选择。"""
    async with aiosqlite.connect(settings.sqlite_db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT receiver_name, receiver_phone, receiver_address FROM orders "
            "WHERE session_id = ? ORDER BY created_at DESC",
            (session_id,),
        ) as cursor:
            rows = await cursor.fetchall()
    seen, result = set(), []
    for r in rows:
        key = (r["receiver_name"], r["receiver_phone"], r["receiver_address"])
        if key not in seen:
            seen.add(key)
            result.append({"receiver_name": r["receiver_name"],
                           "receiver_phone": r["receiver_phone"],
                           "receiver_address": r["receiver_address"]})
        if len(result) >= 3:
            break
    return result


async def order_get(order_id: str) -> Optional[dict]:
    async with aiosqlite.connect(settings.sqlite_db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM orders WHERE order_id = ?", (order_id,)
        ) as cursor:
            row = await cursor.fetchone()
        if not row:
            return None
        order = dict(row)
        async with db.execute(
            "SELECT * FROM order_items WHERE order_id = ?", (order_id,)
        ) as cursor:
            items = [dict(r) for r in await cursor.fetchall()]
    order["items"] = items
    return order
