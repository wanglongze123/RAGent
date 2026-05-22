"""
FastAPI 入口 — 应用启动 / 关闭 / 路由注册 / 静态文件挂载。
"""
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.config import settings
from app.db.product_repo import product_repo
from app.db.vector_store import get_vector_store
from app.api import chat


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用启动时加载商品数据，关闭时清理"""
    print(f"[startup] 环境: {settings.environment}")
    print(f"[startup] 数据集路径: {settings.dataset_path}")

    n = product_repo.load()
    print(f"[startup] 商品仓库: 已加载 {n} 条商品")

    vs = get_vector_store("products")
    print(f"[startup] 向量库: 当前 chunk 数量 = {vs.count()}")
    if vs.count() == 0:
        print("[startup] ⚠ 向量库为空，请先运行: python -m scripts.build_index")

    yield

    print("[shutdown] bye")


app = FastAPI(
    title="RAGent API",
    description="基于 RAG 的多模态电商智能导购 AI Agent",
    version="0.1.0",
    lifespan=lifespan,
)

# CORS — 开发期完全放开，生产环境收紧白名单
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 商品图片静态服务 — 客户端通过 /static/images/xxx.jpg 访问
images_root = settings.dataset_path
if images_root.exists():
    app.mount("/static/images", StaticFiles(directory=str(images_root)), name="images")


# 路由注册
app.include_router(chat.router, prefix="/api/v1", tags=["chat"])


@app.get("/")
async def root():
    return {
        "name": "RAGent",
        "version": "0.1.0",
        "vector_store": settings.vector_store,
        "products_loaded": product_repo.count(),
    }


@app.get("/health")
async def health():
    return {"status": "ok"}
