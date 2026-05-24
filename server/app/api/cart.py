"""
购物车 + 商品 REST API
GET    /api/v1/products/{id}     — 商品详情
GET    /api/v1/cart              — 查看购物车
POST   /api/v1/cart              — 加购商品
PUT    /api/v1/cart/{item_id}    — 修改数量
DELETE /api/v1/cart/{item_id}    — 删除条目
DELETE /api/v1/cart              — 清空购物车
"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.db import relational as db
from app.db.product_repo import product_repo

router = APIRouter()


# ───── 商品详情 ─────

@router.get("/products/{product_id}")
async def get_product(product_id: str):
    product = product_repo.get(product_id)
    if not product:
        raise HTTPException(status_code=404, detail="商品不存在")
    data = product.model_dump()
    data["image_url"] = product.image_url
    data.pop("image_path", None)
    if product.rag_knowledge:
        data["marketing_description"] = product.rag_knowledge.marketing_description
        data["faq"] = [{"question": f.question, "answer": f.answer}
                       for f in product.rag_knowledge.official_faq]
    else:
        data["marketing_description"] = None
        data["faq"] = []
    return data


# ───── 购物车 ─────

class CartAddRequest(BaseModel):
    session_id: str
    product_id: str
    sku_id: str | None = None
    quantity: int = 1


class CartUpdateRequest(BaseModel):
    session_id: str
    quantity: int


@router.get("/cart")
async def get_cart(session_id: str):
    return await db.cart_get(session_id)


@router.post("/cart")
async def add_to_cart(req: CartAddRequest):
    product = product_repo.get(req.product_id)
    if not product:
        raise HTTPException(status_code=404, detail="商品不存在")

    sku_id = req.sku_id
    if not sku_id:
        if len(product.skus) == 1:
            sku_id = product.skus[0].sku_id
        else:
            raise HTTPException(status_code=400, detail="该商品有多个规格，请指定 sku_id")

    sku = next((s for s in product.skus if s.sku_id == sku_id), None)
    if not sku:
        raise HTTPException(status_code=404, detail="SKU 不存在")

    item = await db.cart_add(
        session_id=req.session_id,
        product_id=req.product_id,
        sku_id=sku.sku_id,
        title=product.title,
        image_url=product.image_url,
        sku_props=sku.properties,
        unit_price=sku.price,
        quantity=req.quantity,
    )
    return item


@router.put("/cart/{item_id}")
async def update_cart_item(item_id: str, req: CartUpdateRequest):
    updated = await db.cart_update_quantity(req.session_id, item_id, req.quantity)
    if not updated:
        raise HTTPException(status_code=404, detail="购物车条目不存在")
    return updated


@router.delete("/cart/{item_id}")
async def delete_cart_item(item_id: str, session_id: str):
    success = await db.cart_remove(session_id, item_id)
    if not success:
        raise HTTPException(status_code=404, detail="购物车条目不存在")
    return {"ok": True}


@router.delete("/cart")
async def clear_cart(session_id: str):
    await db.cart_clear(session_id)
    return {"ok": True}
