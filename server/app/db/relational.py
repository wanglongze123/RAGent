"""
关系型数据层 — 支持 SQLite（本地开发）和 MySQL（生产）。
通过 DB_TYPE 环境变量切换，业务方法接口不变。
"""
import json
import uuid
from datetime import datetime
from typing import Optional

from app.config import settings

# ───────────────────────── 连接池（MySQL）─────────────────────────

_mysql_pool = None


async def _get_pool():
    global _mysql_pool
    if _mysql_pool is None:
        import aiomysql
        _mysql_pool = await aiomysql.create_pool(
            host=settings.mysql_host,
            port=settings.mysql_port,
            user=settings.mysql_user,
            password=settings.mysql_password,
            db=settings.mysql_database,
            charset="utf8mb4",
            autocommit=False,
            minsize=2,
            maxsize=10,
            connect_timeout=10,
        )
    return _mysql_pool


def _now() -> str:
    return datetime.utcnow().isoformat()


# ───────────────────────── 统一执行器 ─────────────────────────

class _DB:
    """根据 DB_TYPE 分发到 SQLite 或 MySQL，统一 %s 占位符"""

    @staticmethod
    def _to_mysql(sql: str) -> str:
        return sql.replace("?", "%s")

    async def execute(self, sql: str, params=()) -> int:
        if settings.db_type == "mysql":
            import aiomysql
            pool = await _get_pool()
            async with pool.acquire() as conn:
                async with conn.cursor() as cur:
                    await cur.execute(self._to_mysql(sql), params)
                    await conn.commit()
                    return cur.rowcount
        else:
            import aiosqlite
            async with aiosqlite.connect(settings.sqlite_db_path) as db:
                cur = await db.execute(sql, params)
                await db.commit()
                return cur.rowcount

    async def fetchone(self, sql: str, params=()) -> Optional[dict]:
        if settings.db_type == "mysql":
            import aiomysql
            pool = await _get_pool()
            async with pool.acquire() as conn:
                async with conn.cursor(aiomysql.DictCursor) as cur:
                    await cur.execute(self._to_mysql(sql), params)
                    return await cur.fetchone()
        else:
            import aiosqlite
            async with aiosqlite.connect(settings.sqlite_db_path) as db:
                db.row_factory = aiosqlite.Row
                async with db.execute(sql, params) as cur:
                    row = await cur.fetchone()
                    return dict(row) if row else None

    async def fetchall(self, sql: str, params=()) -> list[dict]:
        if settings.db_type == "mysql":
            import aiomysql
            pool = await _get_pool()
            async with pool.acquire() as conn:
                async with conn.cursor(aiomysql.DictCursor) as cur:
                    await cur.execute(self._to_mysql(sql), params)
                    return list(await cur.fetchall())
        else:
            import aiosqlite
            async with aiosqlite.connect(settings.sqlite_db_path) as db:
                db.row_factory = aiosqlite.Row
                async with db.execute(sql, params) as cur:
                    return [dict(r) for r in await cur.fetchall()]

    async def executemany(self, sql: str, params_list: list) -> None:
        if settings.db_type == "mysql":
            import aiomysql
            pool = await _get_pool()
            async with pool.acquire() as conn:
                async with conn.cursor() as cur:
                    await cur.executemany(self._to_mysql(sql), params_list)
                    await conn.commit()
        else:
            import aiosqlite
            async with aiosqlite.connect(settings.sqlite_db_path) as db:
                await db.executemany(sql, params_list)
                await db.commit()


db = _DB()

# ───────────────────────── 建表 SQL ─────────────────────────

_CREATE_SQLITE = """
CREATE TABLE IF NOT EXISTS sessions (
    session_id          TEXT PRIMARY KEY,
    agent_state         TEXT NOT NULL DEFAULT 'browsing',
    last_shown_products TEXT NOT NULL DEFAULT '[]',
    order_state         TEXT NOT NULL DEFAULT '{}',
    scene_context       TEXT NOT NULL DEFAULT '{}',
    search_state        TEXT NOT NULL DEFAULT '{}',
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS messages (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id  TEXT NOT NULL,
    role        TEXT NOT NULL,
    content     TEXT NOT NULL,
    blocks      TEXT NOT NULL DEFAULT '[]',
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

_CREATE_MYSQL = """
CREATE TABLE IF NOT EXISTS sessions (
    session_id          VARCHAR(64)   PRIMARY KEY,
    agent_state         VARCHAR(32)   NOT NULL DEFAULT 'browsing',
    last_shown_products MEDIUMTEXT    NOT NULL,
    order_state         MEDIUMTEXT    NOT NULL,
    scene_context       MEDIUMTEXT    NOT NULL,
    search_state        MEDIUMTEXT    NOT NULL,
    created_at          VARCHAR(32)   NOT NULL,
    updated_at          VARCHAR(32)   NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS messages (
    id          BIGINT        AUTO_INCREMENT PRIMARY KEY,
    session_id  VARCHAR(64)   NOT NULL,
    role        VARCHAR(16)   NOT NULL,
    content     MEDIUMTEXT    NOT NULL,
    blocks      MEDIUMTEXT    NOT NULL,
    created_at  VARCHAR(32)   NOT NULL,
    INDEX idx_session (session_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS cart_items (
    cart_item_id VARCHAR(64)   PRIMARY KEY,
    session_id   VARCHAR(64)   NOT NULL,
    product_id   VARCHAR(64)   NOT NULL,
    sku_id       VARCHAR(64)   NOT NULL,
    title        TEXT          NOT NULL,
    image_url    TEXT          NOT NULL,
    sku_props    TEXT          NOT NULL,
    unit_price   DOUBLE        NOT NULL,
    quantity     INT           NOT NULL DEFAULT 1,
    created_at   VARCHAR(32)   NOT NULL,
    INDEX idx_session (session_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS orders (
    order_id         VARCHAR(64)  PRIMARY KEY,
    session_id       VARCHAR(64)  NOT NULL,
    status           VARCHAR(32)  NOT NULL DEFAULT 'confirmed',
    receiver_name    VARCHAR(64)  NOT NULL,
    receiver_phone   VARCHAR(32)  NOT NULL,
    receiver_address TEXT         NOT NULL,
    total_price      DOUBLE       NOT NULL,
    created_at       VARCHAR(32)  NOT NULL,
    INDEX idx_session (session_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS order_items (
    id           BIGINT       AUTO_INCREMENT PRIMARY KEY,
    order_id     VARCHAR(64)  NOT NULL,
    product_id   VARCHAR(64)  NOT NULL,
    sku_id       VARCHAR(64)  NOT NULL,
    title        TEXT         NOT NULL,
    quantity     INT          NOT NULL,
    unit_price   DOUBLE       NOT NULL,
    INDEX idx_order (order_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
"""


async def init_db() -> None:
    if settings.db_type == "mysql":
        import aiomysql
        pool = await _get_pool()
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                for stmt in _CREATE_MYSQL.split(";"):
                    stmt = stmt.strip()
                    if stmt:
                        await cur.execute(stmt)
                await conn.commit()
        print("[startup] MySQL: 数据库初始化完成")
    else:
        import aiosqlite
        async with aiosqlite.connect(settings.sqlite_db_path) as db_conn:
            await db_conn.executescript(_CREATE_SQLITE)
            # 增量迁移（老库补字段）
            for col, default in [
                ("order_state", "'{}'"),
                ("scene_context", "'{}'"),
                ("search_state", "'{}'"),
            ]:
                cur = await db_conn.execute("PRAGMA table_info(sessions)")
                cols = [r[1] for r in await cur.fetchall()]
                if col not in cols:
                    await db_conn.execute(
                        f"ALTER TABLE sessions ADD COLUMN {col} TEXT NOT NULL DEFAULT {default}"
                    )
            cur = await db_conn.execute("PRAGMA table_info(messages)")
            if "blocks" not in [r[1] for r in await cur.fetchall()]:
                await db_conn.execute(
                    "ALTER TABLE messages ADD COLUMN blocks TEXT NOT NULL DEFAULT '[]'"
                )
            await db_conn.commit()
        print("[startup] SQLite: 数据库初始化完成")


# ───────────────────────── 会话 ─────────────────────────

async def create_session(session_id: str) -> dict:
    now = _now()
    await db.execute(
        "INSERT INTO sessions (session_id, agent_state, last_shown_products, "
        "order_state, scene_context, search_state, created_at, updated_at) "
        "VALUES (?, 'browsing', '[]', '{}', '{}', '{}', ?, ?)",
        (session_id, now, now),
    )
    return {"session_id": session_id, "agent_state": "browsing", "created_at": now}


async def get_session(session_id: str) -> Optional[dict]:
    row = await db.fetchone("SELECT * FROM sessions WHERE session_id = ?", (session_id,))
    if not row:
        return None
    row["last_shown_products"] = json.loads(row["last_shown_products"] or "[]")
    row["order_state"] = json.loads(row.get("order_state") or "{}")
    row["scene_context"] = json.loads(row.get("scene_context") or "{}")
    row["search_state"] = json.loads(row.get("search_state") or "{}")
    return row


async def _update_session_json(session_id: str, field: str, value: dict) -> None:
    await db.execute(
        f"UPDATE sessions SET {field} = ?, updated_at = ? WHERE session_id = ?",
        (json.dumps(value, ensure_ascii=False), _now(), session_id),
    )


async def update_order_state(session_id: str, order_state: dict) -> None:
    await _update_session_json(session_id, "order_state", order_state)


async def clear_order_state(session_id: str) -> None:
    await update_order_state(session_id, {})


async def save_scene_context(session_id: str, scene_context: dict) -> None:
    await _update_session_json(session_id, "scene_context", scene_context)


async def clear_scene_context(session_id: str) -> None:
    await save_scene_context(session_id, {})


async def update_search_state(session_id: str, search_state: dict) -> None:
    await _update_session_json(session_id, "search_state", search_state)


async def clear_search_state(session_id: str) -> None:
    await update_search_state(session_id, {})


async def update_session_state(
    session_id: str,
    agent_state: Optional[str] = None,
    last_shown_products: Optional[list] = None,
) -> None:
    parts, vals = [], []
    if agent_state is not None:
        parts.append("agent_state = ?")
        vals.append(agent_state)
    if last_shown_products is not None:
        parts.append("last_shown_products = ?")
        vals.append(json.dumps(last_shown_products, ensure_ascii=False))
    if not parts:
        return
    parts.append("updated_at = ?")
    vals.extend([_now(), session_id])
    await db.execute(f"UPDATE sessions SET {', '.join(parts)} WHERE session_id = ?", vals)


# ───────────────────────── 对话历史 ─────────────────────────

async def add_message(session_id: str, role: str, content: str, blocks: Optional[list] = None) -> None:
    await db.execute(
        "INSERT INTO messages (session_id, role, content, blocks, created_at) VALUES (?, ?, ?, ?, ?)",
        (session_id, role, content, json.dumps(blocks or [], ensure_ascii=False), _now()),
    )


async def get_recent_messages(session_id: str, limit: int = 10) -> list[dict]:
    rows = await db.fetchall(
        "SELECT role, content, created_at FROM messages "
        "WHERE session_id = ? ORDER BY id DESC LIMIT ?",
        (session_id, limit),
    )
    return list(reversed(rows))


async def get_all_messages(session_id: str) -> list[dict]:
    rows = await db.fetchall(
        "SELECT role, content, blocks, created_at FROM messages WHERE session_id = ? ORDER BY id ASC",
        (session_id,),
    )
    for r in rows:
        r["blocks"] = json.loads(r.get("blocks") or "[]")
    return rows


async def list_sessions() -> list[dict]:
    sql = """
        SELECT
            s.session_id,
            s.created_at,
            s.updated_at,
            (SELECT content FROM messages m
             WHERE m.session_id = s.session_id AND m.role = 'user'
             ORDER BY m.id ASC LIMIT 1) AS preview,
            (SELECT MAX(created_at) FROM messages m
             WHERE m.session_id = s.session_id) AS last_msg_at
        FROM sessions s
        WHERE EXISTS (SELECT 1 FROM messages m WHERE m.session_id = s.session_id)
        ORDER BY last_msg_at DESC
    """
    rows = await db.fetchall(sql)
    return [
        {
            "session_id": r["session_id"],
            "preview": r.get("preview") or "",
            "created_at": r["created_at"],
            "updated_at": r["updated_at"],
        }
        for r in rows
    ]


async def delete_session(session_id: str) -> None:
    for tbl in ("messages", "cart_items", "orders", "order_items"):
        col = "order_id" if tbl == "order_items" else "session_id"
        if tbl == "order_items":
            # 先拿本会话的 order_id 列表再删
            orders = await db.fetchall(
                "SELECT order_id FROM orders WHERE session_id = ?", (session_id,)
            )
            for o in orders:
                await db.execute("DELETE FROM order_items WHERE order_id = ?", (o["order_id"],))
        else:
            await db.execute(f"DELETE FROM {tbl} WHERE session_id = ?", (session_id,))
    await db.execute("DELETE FROM sessions WHERE session_id = ?", (session_id,))


# ───────────────────────── 购物车 ─────────────────────────

async def cart_add(
    session_id: str, product_id: str, sku_id: str,
    title: str, image_url: str, sku_props: dict,
    unit_price: float, quantity: int = 1,
) -> dict:
    existing = await db.fetchone(
        "SELECT cart_item_id, quantity FROM cart_items WHERE session_id = ? AND sku_id = ?",
        (session_id, sku_id),
    )
    if existing:
        new_qty = existing["quantity"] + quantity
        await db.execute(
            "UPDATE cart_items SET quantity = ? WHERE cart_item_id = ?",
            (new_qty, existing["cart_item_id"]),
        )
        cart_item_id, quantity = existing["cart_item_id"], new_qty
    else:
        cart_item_id = f"ci_{uuid.uuid4().hex[:12]}"
        await db.execute(
            "INSERT INTO cart_items "
            "(cart_item_id, session_id, product_id, sku_id, title, image_url, sku_props, unit_price, quantity, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (cart_item_id, session_id, product_id, sku_id, title, image_url,
             json.dumps(sku_props, ensure_ascii=False), unit_price, quantity, _now()),
        )
    return {
        "cart_item_id": cart_item_id, "product_id": product_id, "sku_id": sku_id,
        "title": title, "image_url": image_url, "sku_props": sku_props,
        "unit_price": unit_price, "quantity": quantity,
    }


async def cart_update_quantity(session_id: str, cart_item_id: str, quantity: int) -> Optional[dict]:
    rows = await db.execute(
        "UPDATE cart_items SET quantity = ? WHERE cart_item_id = ? AND session_id = ?",
        (quantity, cart_item_id, session_id),
    )
    if not rows:
        return None
    return await _get_cart_item(cart_item_id)


async def cart_remove(session_id: str, cart_item_id: str) -> bool:
    n = await db.execute(
        "DELETE FROM cart_items WHERE cart_item_id = ? AND session_id = ?",
        (cart_item_id, session_id),
    )
    return bool(n)


async def cart_clear(session_id: str) -> None:
    await db.execute("DELETE FROM cart_items WHERE session_id = ?", (session_id,))


async def cart_get(session_id: str) -> dict:
    rows = await db.fetchall(
        "SELECT * FROM cart_items WHERE session_id = ? ORDER BY created_at ASC", (session_id,)
    )
    items = []
    for r in rows:
        r["sku_props"] = json.loads(r["sku_props"])
        r["subtotal"] = round(r["unit_price"] * r["quantity"], 2)
        items.append(r)
    total_count = sum(i["quantity"] for i in items)
    total_price = round(sum(i["subtotal"] for i in items), 2)
    return {"session_id": session_id, "items": items, "total_count": total_count, "total_price": total_price}


async def _get_cart_item(cart_item_id: str) -> Optional[dict]:
    r = await db.fetchone("SELECT * FROM cart_items WHERE cart_item_id = ?", (cart_item_id,))
    if not r:
        return None
    r["sku_props"] = json.loads(r["sku_props"])
    r["subtotal"] = round(r["unit_price"] * r["quantity"], 2)
    return r


# ───────────────────────── 订单 ─────────────────────────

async def order_create(
    session_id: str, receiver_name: str, receiver_phone: str,
    receiver_address: str, items: list[dict], total_price: float,
) -> dict:
    order_id = f"ord_{datetime.utcnow().strftime('%Y%m%d%H%M%S')}_{uuid.uuid4().hex[:6]}"
    now = _now()
    await db.execute(
        "INSERT INTO orders "
        "(order_id, session_id, status, receiver_name, receiver_phone, receiver_address, total_price, created_at) "
        "VALUES (?, ?, 'confirmed', ?, ?, ?, ?, ?)",
        (order_id, session_id, receiver_name, receiver_phone, receiver_address, total_price, now),
    )
    await db.executemany(
        "INSERT INTO order_items (order_id, product_id, sku_id, title, quantity, unit_price) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        [(order_id, i["product_id"], i["sku_id"], i.get("title", ""), i["quantity"], i["unit_price"])
         for i in items],
    )
    return {"order_id": order_id, "status": "confirmed", "message": "订单提交成功",
            "total_price": total_price, "created_at": now}


async def get_used_addresses(session_id: str) -> list[dict]:
    rows = await db.fetchall(
        "SELECT receiver_name, receiver_phone, receiver_address FROM orders "
        "WHERE session_id = ? ORDER BY created_at DESC", (session_id,)
    )
    seen, result = set(), []
    for r in rows:
        key = (r["receiver_name"], r["receiver_phone"], r["receiver_address"])
        if key not in seen:
            seen.add(key)
            result.append({k: r[k] for k in ("receiver_name", "receiver_phone", "receiver_address")})
        if len(result) >= 3:
            break
    return result


async def list_orders(session_id: str) -> list[dict]:
    orders = await db.fetchall(
        "SELECT * FROM orders WHERE session_id = ? ORDER BY created_at DESC", (session_id,)
    )
    for order in orders:
        order["items"] = await db.fetchall(
            "SELECT * FROM order_items WHERE order_id = ?", (order["order_id"],)
        )
    return orders


async def order_get(order_id: str) -> Optional[dict]:
    order = await db.fetchone("SELECT * FROM orders WHERE order_id = ?", (order_id,))
    if not order:
        return None
    order["items"] = await db.fetchall(
        "SELECT * FROM order_items WHERE order_id = ?", (order_id,)
    )
    return order
