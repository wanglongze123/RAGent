# RAGent — 多模态电商智能导购 AI Agent

> 字节跳动 AI 全栈挑战赛 · 基于 RAG 的多模态电商智能导购 AI Agent

## 项目简介

RAGent 是一个面向电商场景的智能导购 AI Agent，将传统"展示型广告"升级为"交互型导购"。用户可通过自然语言或拍照与 Agent 对话，实现从内容浏览到购买决策的深度连接。

## 核心能力

- **多轮对话导购**：理解复杂意图，支持追问、反问、上下文记忆
- **混合检索 RAG**：向量 + BM25 + 结构化过滤三路召回，Reranker 精排
- **反选排除**：精准解析"不要日系""不含酒精"等否定条件
- **多商品对比**：自动提取对比维度，生成结构化对比表格
- **拍照找货**：上传图片，视觉向量相似检索同款商品
- **购物车闭环**：对话式加购、管理购物车、模拟下单全链路

## 技术架构

```
客户端（Android Kotlin + Jetpack Compose）
         ↕ SSE 结构化事件流
后端（Python FastAPI）
├── 编排层：Hierarchical Multi-Agent + 状态机
├── 能力层：Hybrid Search + Reranker + Query 改写
├── 模型层：Doubao-Seed-2.0-lite + Doubao-embedding-vision
└── 存储层：Chroma / VikingDB + SQLite / MySQL
```

## 目录结构

```
RAGent/
├── client/          # Android 客户端
├── server/          # Python 后端
│   ├── app/         # 应用代码
│   ├── scripts/     # 离线脚本（建库、评估）
│   ├── tests/       # 单元测试
│   ├── Dockerfile
│   └── requirements.txt
├── docs/
│   └── 接口文档.md
└── docker-compose.yml
```

## 快速启动（本地开发）

### 后端

```bash
cd server
cp .env.example .env        # 填入 API Key
pip install -r requirements.txt
python scripts/build_index.py   # 数据入库（首次运行）
uvicorn app.main:app --reload --port 8000
```

### Android 客户端

用 Android Studio 打开 `client/` 目录，修改 `BaseUrl` 为 `http://10.0.2.2:8000`（模拟器）或局域网 IP（真机）。

### Docker 启动

```bash
cp server/.env.example server/.env   # 填入 API Key
docker-compose up --build
```

## 接口文档

见 [docs/接口文档.md](docs/接口文档.md)

## 分支说明

| 分支 | 说明 |
|---|---|
| `main` | 稳定版本 |
| `dev/server` | 后端开发分支 |
| `dev/client` | 客户端开发分支 |
