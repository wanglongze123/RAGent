# RAGent — 多模态电商智能导购 AI Agent

> 字节跳动 AI 全栈挑战赛 · 基于 RAG 的多模态电商智能导购 AI Agent

---

## 快速体验

后端已部署在云端服务器，**安装 APK 即可直接体验，无需任何配置**。

1. 下载 [`app-debug.apk`](app-debug.apk)，安装到 Android 7.0+ 手机
2. 打开 App，即连云端后端，开始对话

---

## 项目简介

RAGent 是一个面向电商场景的智能导购 AI Agent，将传统"展示型广告"升级为"交互型导购"。用户通过自然语言或拍照与 Agent 对话，实现从浏览兴趣到购买决策的全链路深度连接。

**已实现能力：**

| 类别 | 能力 |
|------|------|
| 对话理解 | 多轮上下文管理、主动反问澄清、意图识别路由 |
| 检索 | 向量 + BM25 + 结构化过滤三路混合检索、RRF 融合、Reranker 精排 |
| 复杂场景 | 否定语义反选、多商品对比、场景化组合推荐 |
| 购物闭环 | 对话式加购、购物车管理、下单全流程 |
| 多模态 | 拍照找货、语音输入（ASR）、TTS 语音播报 |

---

## 技术架构

```
┌─────────────────────────────────────────┐
│       Android 客户端（Kotlin/Compose）    │
└──────────────────┬──────────────────────┘
                   │ SSE 结构化事件流
┌──────────────────▼──────────────────────┐
│           后端（Python FastAPI）          │
│                                         │
│  编排层：Master Agent + 6 子 Agent       │
│          规则快路由 + 状态机驱动          │
│  能力层：向量 + BM25 + RRF + Reranker    │
│  模型层：Doubao-Seed-2.0-lite            │
│          Doubao-embedding-vision         │
│  存储层：Qdrant · MySQL · Redis          │
└─────────────────────────────────────────┘
```

---

## 文档

| 文档 | 说明 |
|------|------|
| [docs/技术文档.md](docs/技术文档.md) | 架构设计、核心实现、部署说明、亮点与创新 |
| [docs/接口文档.md](docs/接口文档.md) | REST + SSE 接口协议详细说明 |

---

## 本地部署（可选）

后端已云端部署，以下步骤供本地完整复现：

**前置条件：** Docker 24.0+、Docker Compose v2.20+、豆包 API Key、BGE Reranker 模型文件

```bash
# 1. 克隆仓库
git clone https://github.com/wanglongze123/RAGent.git
cd RAGent

# 2. 配置环境变量
cp server/.env.example server/.env
# 编辑 server/.env，填入 DOUBAO_API_KEY、DOUBAO_MODEL、DOUBAO_EMBEDDING_MODEL

# 3. 首次建向量索引（约 3-5 分钟）
cd server && pip install -r requirements.txt
python scripts/build_index.py && cd ..

# 4. 一键启动（mysql + redis + qdrant + backend）
docker compose up -d --build

# 验证：curl http://localhost:8000/health
```

详细配置说明见 [docs/技术文档.md](docs/技术文档.md)。

---

## 团队

| 成员 | 负责模块 |
|------|---------|
| 王龙泽 | 后端：RAG 检索管线、多 Agent 编排、LLM 集成、Docker 部署 |
| 孙贺 | Android 客户端：UI/UX、SSE 流式渲染、多模态采集、购物车与订单 |

---

## 分支说明

| 分支 | 说明 |
|------|------|
| `main` | 稳定版本 |
| `dev/server` | 后端开发分支 |
| `dev/client` | 客户端开发分支 |
