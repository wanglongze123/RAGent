# RAGent — 多模态电商智能导购 AI Agent

> 字节跳动 AI 全栈挑战赛 · 基于 RAG 的多模态电商智能导购 AI Agent

---

## 目录

1. [项目简介](#1-项目简介)
2. [团队分工](#2-团队分工)
3. [系统架构](#3-系统架构)
4. [技术栈](#4-技术栈)
5. [依赖环境](#5-依赖环境)
6. [目录结构](#6-目录结构)
7. [配置说明](#7-配置说明)
8. [部署与快速体验](#8-部署与快速体验)
9. [使用说明](#9-使用说明)
10. [核心实现](#10-核心实现)
11. [亮点与创新](#11-亮点与创新)

---

## 1. 项目简介

RAGent 是一个面向电商场景的智能导购 AI Agent，将传统"展示型广告"升级为"交互型导购"。用户通过自然语言或拍照与 Agent 对话，实现从浏览兴趣到购买决策的全链路深度连接。

**已实现能力：**

| 类别 | 能力 |
|------|------|
| 对话理解 | 多轮上下文管理、主动反问澄清、意图识别路由 |
| 检索 | 向量 + BM25 + 结构化过滤三路混合检索、Reranker 精排 |
| 复杂场景 | 否定语义反选、多商品对比、场景化组合推荐 |
| 购物闭环 | 对话式加购、购物车管理、下单全流程 |
| 多模态 | 拍照找货、语音输入（ASR）、TTS 语音播报 |
| 工程 | 检索结果 Redis 缓存、设备级会话隔离、全平台 Docker 部署 |

**代码仓库：** https://github.com/wanglongze123/RAGent

**快速体验（零部署）：** 安装 `app-debug.apk`，打开即连云端后端，无需任何配置。

---

## 2. 团队分工

| 成员 | 负责模块 |
|------|---------|
| 王龙泽 | 后端全栈：RAG 检索管线、多 Agent 编排、LLM 集成、数据建库、Docker 部署 |
| 孙贺 | Android 客户端：UI/UX、SSE 流式渲染、多模态采集（拍照/语音/TTS）、购物车与订单页面 |

---

## 3. 系统架构

### 3.1 整体分层

```
┌──────────────────────────────────────────┐
│          Android 客户端（Kotlin）          │
│  Jetpack Compose UI · MVVM · SSE 流式渲染  │
└───────────────────┬──────────────────────┘
                    │ HTTP / SSE（结构化事件流）
                    │ X-Device-Id 设备隔离头
┌───────────────────▼──────────────────────┐
│              后端（Python FastAPI）        │
│                                          │
│  ┌─────────────────────────────────────┐ │
│  │          编排层（Agent）             │ │
│  │  Master Agent                       │ │
│  │  ├── 意图分类（规则快路由 + LLM兜底）│ │
│  │  ├── 状态机路由（4 状态转移表）      │ │
│  │  └── 6 个子 Agent（各司其职）        │ │
│  │      search / compare / scene /     │ │
│  │      cart / order / product_inquiry │ │
│  └─────────────────┬───────────────────┘ │
│  ┌─────────────────▼───────────────────┐ │
│  │          能力层（RAG）               │ │
│  │  混合检索（向量 + BM25 + 过滤）      │ │
│  │  RRF 融合 → 商品级 Reranker 精排     │ │
│  │  Query 扩展 · 同义词词典            │ │
│  └─────────────────┬───────────────────┘ │
│  ┌─────────────────▼───────────────────┐ │
│  │          模型层（豆包 API）           │ │
│  │  文本生成  Doubao-Seed-2.0-lite      │ │
│  │  向量化    Doubao-embedding-vision   │ │
│  └─────────────────┬───────────────────┘ │
│  ┌─────────────────▼───────────────────┐ │
│  │          存储层                      │ │
│  │  Qdrant（向量库）· MySQL（关系库）   │ │
│  │  Redis（检索缓存）                   │ │
│  └─────────────────────────────────────┘ │
└──────────────────────────────────────────┘
```

### 3.2 一次对话的完整数据流

```
用户输入（文字 / 图片）
    │
    ▼
[Master Agent] 意图分类 → 状态机路由
    │
    ▼
[子 Agent] 构造检索 Query + 过滤条件
    │
    ▼
[HybridRetriever] 向量检索 ‖ BM25 检索 → RRF 融合 → Reranker 精排
    │
    ▼
[LLM Middleware] 注入商品资料 + System Prompt → 流式生成
    │
    ▼
[SSE 事件流] thinking / text_delta / product_card_list /
             comparison_table / clarification / cart_update / done
    │
    ▼
[Android 客户端] 逐事件渲染：流式文字气泡 / 商品卡片 / 对比表格 / 选项按钮
```

---

## 4. 技术栈

### 后端

| 层次 | 技术选型 | 版本 |
|------|---------|------|
| Web 框架 | FastAPI + Uvicorn | 0.115.0 / 0.32.0 |
| 大语言模型 | Doubao-Seed-2.0-lite（豆包 API，兼容 OpenAI SDK） | — |
| Embedding | Doubao-embedding-vision（文本 + 图像同空间 2048 维） | — |
| 向量库 | Qdrant（生产） / Chroma（本地开发） | v1.12.4 / 0.5.18 |
| 关系数据库 | MySQL（生产） / SQLite（本地开发） | 8.0 / — |
| 缓存 | Redis | 7-alpine |
| Reranker | BGE-Reranker-Base（本地 Cross-Encoder） | — |
| BM25 | rank-bm25 + jieba 中文分词 | 0.2.2 / 0.42.1 |
| 数据校验 | Pydantic v2 | 2.9.2 |
| 异步 IO | aiomysql / aiosqlite / httpx | — |
| 容器化 | Docker + Docker Compose | — |

### 客户端（Android）

| 层次 | 技术选型 |
|------|---------|
| 语言 | Kotlin |
| UI 框架 | Jetpack Compose（Material 3，100% Compose，无 XML） |
| 架构 | MVVM + Repository Pattern |
| 异步 | Kotlin Coroutines + StateFlow |
| 网络 | OkHttp（SSE 长连接 + REST） |
| 本地存储 | DataStore Preferences（会话）+ SharedPreferences（DeviceId） |
| 图片加载 | Coil Compose |
| 多模态 | Android CameraX / ActivityResult API / TextToSpeech |
| 导航 | Jetpack Navigation Compose |
| 序列化 | Gson |
| 最低 SDK | API 24（Android 7.0） |

---

## 5. 依赖环境

### 后端运行环境

| 依赖 | 版本要求 |
|------|---------|
| Python | 3.11+ |
| Docker | 24.0+ |
| Docker Compose | v2.20+ |
| 豆包 API Key | 需提前申请并写入 `.env` |
| BGE Reranker 模型文件 | `bge-reranker-base`，放置于 `server/models/Xorbits/bge-reranker-base/` |
| 向量库数据（可选） | 若需本地复现，需先运行 `python scripts/build_index.py` 建库 |

### 客户端运行环境

| 依赖 | 版本要求 |
|------|---------|
| Android | 7.0（API 24）及以上 |
| 安装方式 | ADB 或直接安装 `app-debug.apk` |

---

## 6. 目录结构

```
RAGent/
├── client/                          # Android 客户端
│   └── app/src/main/java/com/ragent/shopping/
│       ├── data/
│       │   ├── local/
│       │   │   ├── SessionPrefs.kt      # 会话 ID + 收货信息持久化（DataStore）
│       │   │   └── DeviceId.kt          # 设备唯一标识（首次生成 UUID，永久复用）
│       │   ├── remote/
│       │   │   ├── SseClient.kt         # SSE 流式客户端（OkHttp EventSource → Flow）
│       │   │   ├── ApiService.kt        # REST API 封装（会话/购物车/订单/商品）
│       │   │   └── NetworkConfig.kt     # BASE_URL 配置（BuildConfig 注入）
│       │   ├── model/
│       │   │   └── Models.kt            # 数据模型：ChatMessage sealed class + SSE 事件枚举
│       │   └── repository/
│       │       ├── ChatRepository.kt    # 对话逻辑：SSE 事件解析 + 会话管理
│       │       └── CartRepository.kt    # 购物车 CRUD
│       ├── ui/
│       │   ├── screen/
│       │   │   ├── ChatScreen.kt        # 主对话页：流式渲染、富消息、多模态输入
│       │   │   ├── ProductDetailScreen.kt # 商品详情底部面板 + SKU 选择
│       │   │   ├── CartScreen.kt        # 购物车
│       │   │   ├── OrderHistoryScreen.kt  # 订单列表
│       │   │   ├── OrderFormBottomSheet.kt # 收货信息表单（含校验）
│       │   │   └── MarkdownContent.kt   # 纯 Compose Markdown 渲染（无第三方库）
│       │   ├── viewmodel/
│       │   │   ├── ChatViewModel.kt     # 会话状态机：消息累积、SSE 事件分发、TTS
│       │   │   └── CartViewModel.kt     # 购物车状态
│       │   └── theme/                   # Material 3 主题（品牌色系）
│       ├── util/
│       │   └── TtsManager.kt            # Android TTS 异步封装
│       ├── Navigation.kt                # Compose 导航图
│       └── MainActivity.kt             # 入口：EdgeToEdge + 初始化
│
├── server/                          # Python 后端
│   ├── app/
│   │   ├── agent/
│   │   │   ├── master_agent.py          # 主编排器：意图分类 + 状态机路由 + 上下文管理
│   │   │   ├── middleware.py            # LLM 调用中间件：Prompt 注入 + 流式 / 非流式
│   │   │   ├── state_machine.py         # 4 状态转移表（browsing/comparing/cart/checkout）
│   │   │   └── sub_agents/
│   │   │       ├── search_agent.py      # 单品搜索 + Slot-filling 反问收敛
│   │   │       ├── compare_agent.py     # 多商品对比（四步：识别→检索→提维度→组表）
│   │   │       ├── scene_agent.py       # 场景化组合推荐（规划 + 主题导航）
│   │   │       ├── cart_agent.py        # 购物车操作（加购/改量/删品）
│   │   │       ├── order_agent.py       # 下单多步确认流程
│   │   │       └── product_inquiry_agent.py # 已展示商品追问
│   │   ├── rag/
│   │   │   ├── hybrid_retriever.py      # 混合检索主入口：向量+BM25 RRF 融合 + 精排 + 缓存
│   │   │   ├── retriever.py             # 向量检索（Qdrant / Chroma 双后端）
│   │   │   ├── bm25_retriever.py        # BM25 检索（jieba 分词 + 同义词扩展）
│   │   │   ├── query_expander.py        # Query 扩展：同义词词典（45+ 高频词）
│   │   │   └── reranker.py             # BGE Cross-Encoder 精排（降级 Doubao API）
│   │   ├── db/
│   │   │   ├── relational.py           # MySQL/SQLite 双后端：会话/消息/购物车/订单
│   │   │   ├── vector_store.py         # 向量库抽象：Qdrant/Chroma 统一接口
│   │   │   ├── product_repo.py         # 商品内存缓存 + 地域品牌索引
│   │   │   └── cache.py               # Redis 检索缓存（可选，降级安全）
│   │   ├── llm/
│   │   │   ├── client.py              # 豆包 API 客户端：文本/流式/Embedding/VLM
│   │   │   └── prompts/               # 各子 Agent 的 System Prompt 模板
│   │   ├── api/
│   │   │   ├── chat.py                # 会话、流式对话、图搜接口
│   │   │   └── cart.py                # 购物车、商品、订单接口
│   │   ├── models/                    # Pydantic 请求/响应模型 + SSE 事件定义
│   │   ├── config.py                  # 配置项（Pydantic Settings，env 注入）
│   │   └── main.py                    # FastAPI 应用入口 + 启动初始化
│   ├── scripts/
│   │   ├── build_index.py             # 商品数据向量化建库脚本
│   │   └── evaluate.py               # 检索效果评估脚本
│   ├── tests/                         # 单元测试
│   ├── Dockerfile
│   └── requirements.txt
│
├── docker-compose.yml                 # 一键编排：mysql + redis + qdrant + backend
├── docs/
│   
│   └── 接口文档.md                    # REST + SSE 接口说明（完整协议见 [docs/接口文档.md](docs/接口文档.md)）
└── README.md
```

---

## 7. 配置说明

后端所有配置通过 `server/.env` 注入，生产环境通过 `docker-compose.yml` 的 `environment` 字段覆盖，**不需要手动改代码**。

### 7.1 `.env` 配置项全览

| 配置项 | 默认值 | 说明 |
|--------|--------|------|
| `DOUBAO_API_KEY` | **必填** | 豆包 API 密钥 |
| `DOUBAO_BASE_URL` | `https://ark.cn-beijing.volces.com/api/v3/` | 豆包 API 地址 |
| `DOUBAO_MODEL` | **必填** | 主模型 Endpoint ID |
| `DOUBAO_FAST_MODEL` | 空（回退主模型） | 轻量模型，用于意图分类 |
| `DOUBAO_EMBEDDING_MODEL` | **必填** | Embedding 模型（含多模态） |
| `DOUBAO_VISION_MODEL` | 空 | 视觉模型（可与 Embedding 复用） |
| `ENVIRONMENT` | `development` | `development` / `production` |
| `DB_TYPE` | `sqlite` | `sqlite`（本地）/ `mysql`（生产） |
| `SQLITE_DB_PATH` | `./app.db` | SQLite 文件路径 |
| `MYSQL_HOST` | `mysql` | MySQL 主机名（Docker 内为服务名） |
| `MYSQL_PORT` | `3306` | — |
| `MYSQL_USER` | `ragent` | — |
| `MYSQL_PASSWORD` | `ragent123` | — |
| `MYSQL_DATABASE` | `ragent` | — |
| `VECTOR_STORE` | `chroma` | `chroma`（本地）/ `qdrant`（生产） |
| `CHROMA_PERSIST_DIR` | `./chroma_db` | Chroma 持久化目录 |
| `QDRANT_HOST` | `qdrant` | Qdrant 主机名 |
| `QDRANT_PORT` | `6333` | — |
| `CACHE_BACKEND` | `none` | `none`（禁用）/ `redis`（启用） |
| `REDIS_HOST` | `redis` | Redis 主机名 |
| `REDIS_PORT` | `6379` | — |
| `DATASET_DIR` | `../ecommerce_agent_dataset` | 商品数据集目录 |
| `RERANKER_ENABLED` | `true` | 是否启用 BGE 精排 |
| `RERANKER_MODEL_PATH` | `./models/Xorbits/bge-reranker-base` | BGE 模型本地路径 |

### 7.2 本地 vs 生产配置对比

| 配置项 | 本地开发 | 生产（Docker） |
|--------|---------|---------------|
| DB | SQLite | MySQL 8.0 |
| 向量库 | Chroma | Qdrant v1.12.4 |
| 缓存 | none（关闭） | Redis 7 |
| Reranker | 本地 BGE 模型 | 本地 BGE 模型 |

---

## 8. 部署与快速体验

### 8.1 快速体验（推荐，零部署）

后端已部署在云端服务器，客户端 APK 直连，**评委无需启动任何服务**。

1. 从代码仓库下载 `app-debug.apk`
2. 安装到 Android 手机（Android 7.0 及以上）
3. 打开 App，即可直接对话

> APK 内已写入后端地址，开箱即用。

### 8.2 本地完整部署（可选）

如需在本地运行完整后端，按以下步骤操作：

**前置条件：**
- Docker 24.0+、Docker Compose v2.20+
- BGE Reranker 模型文件（`bge-reranker-base`）
- 商品数据集（`ecommerce_agent_dataset/`）
- 豆包 API Key

**步骤：**

```bash
# 1. 克隆仓库
git clone https://github.com/wanglongze123/RAGent.git
cd RAGent

# 2. 填写配置
cp server/.env.example server/.env
# 编辑 server/.env，填入 DOUBAO_API_KEY、DOUBAO_MODEL、DOUBAO_EMBEDDING_MODEL

# 3. 建向量索引（首次需要，约 3-5 分钟）
cd server
pip install -r requirements.txt
python scripts/build_index.py

# 4. 启动全部服务（mysql + redis + qdrant + backend）
cd ..
docker compose up -d --build

# 后端启动后监听 http://localhost:8000
```

**验证启动：**
```bash
curl http://localhost:8000/health
# 返回 {"status": "ok"} 即为成功
```

**客户端连接本地后端：**

修改 `client/app/build.gradle.kts`，将 `BASE_URL` 改为 `http://10.0.2.2:8000`（模拟器）或本机 IP（真机），重新编译安装。

---

## 9. 使用说明

以下场景覆盖需求文档中所有难度等级，评委可按顺序体验：

### 基础场景

**单轮模糊推荐：**
> 推荐一款适合油皮的洗面奶

**条件筛选：**
> 200 元以下的蓝牙耳机有哪些？

### 进阶场景

**多轮追问与细化：**
> 帮我推荐跑鞋
> （Agent 反问预算/风格后继续）→ 要轻量的，预算 500 以内

**多商品对比：**
> 帮我对比一下刚才推荐的那几款面霜，哪个更保湿？

**Agent 主动反问：**
> 推荐一款手机
> （Agent 会主动询问使用场景、预算、品牌偏好，逐步锁定需求）

### 高级场景

**反选 / 排除约束：**
> 推荐防晒霜，不要含酒精、不要日系品牌

**场景化组合推荐：**
> 下周去三亚度假，帮我搭配一套防晒和穿搭方案

**购物车与下单全链路：**
> 把第一款加入购物车
> （进入购物车页）→ 删掉第二件，修改数量
> 结算 → 填写收货信息 → 确认下单

### 多模态场景

**拍照找货：**
> 点击相机图标，拍一张或上传服装图片，Agent 自动检索相似商品

**语音输入：**
> 点击麦克风（系统键盘语音），说出购物需求

---

## 10. 核心实现

### 10.1 分层多 Agent 编排 + 状态机驱动

系统采用 **Master Agent + 6 个子 Agent** 的分层架构，通过显式状态机约束会话流转，而非全程依赖 LLM 决策。

**意图识别：三层快路由**

```
用户输入
    │
    ├─① 图搜快捷通道：有图片 → 直接走搜索（无需 LLM）
    │
    ├─② 规则快速通道：匹配购物车/下单/对比/位置词等关键词 → 秒级判定
    │   覆盖 99% 电商常见意图，跳过 LLM 调用
    │
    └─③ LLM 兜底：仅在规则无法判定的语义模糊场景调用
```

规则快路由大幅降低首 Token 延迟，LLM 仅在必要时调用。

**状态机（4 状态）**

```
browsing（浏览/搜索）
    │ 加购
    ▼
cart_management（购物车管理）
    │ 结算
    ▼
checkout（下单确认）   ←→   comparing（对比）
```

每个状态明确约束允许的 Agent 和意图跳转，防止对话在错误时机跳转（如下单过程中被无关意图打断）。

**6 个子 Agent 职责：**

| Agent | 负责 |
|-------|------|
| `search_agent` | 单品搜索 + Slot-filling 式渐进反问 |
| `compare_agent` | 多商品对比（四步法：识别→并行检索→提维度→组表→流式理由） |
| `scene_agent` | 场景化组合规划 + 多主题导航 |
| `cart_agent` | 购物车 CRUD（加购/改量/删品） |
| `order_agent` | 下单多步确认（购物车→表单→二次确认→提交） |
| `product_inquiry_agent` | 已展示商品的追问回答 |

**渐进反问（Slot-filling）**

`search_agent` 内置三层反问决策：当候选商品 > 3 个且尚未充分锁定需求时，依次询问：

1. **动态 SKU 属性**：从候选商品中提取有区分力的属性选项（如颜色/尺码/容量）
2. **类目关键维度**：按品类预设询问维度（手机类询问"用途/品牌/预算"，运动鞋询问"场景/性别/价位"等）
3. **兜底**：直接出卡

用户的反问答复通过 `pending` 机制**确定性解析**（不经 LLM），选项点击即可精准识别；同时提供"直接帮我搜"按钮随时退出反问。

### 10.2 混合检索 + RRF 融合 + Reranker 精排

**Chunking 策略**

每条商品数据切分为 4 类 chunk，独立向量化：

| Chunk 类型 | 内容 | 适合命中 |
|-----------|------|---------|
| `base` | 标题 + 品牌 + 类目 + 价格 | 精确品名/品牌检索 |
| `description` | 营销描述、卖点文案 | 功效/适用人群/场景 |
| `faq` | 官方问答（每条独立 chunk） | 具体功效/成分/规格 |
| `review` | 用户评价（每条独立 chunk） | 真实体验/口碑 |

100 条商品 → 约 1,092 个 chunk，元数据保留 `product_id/brand/category/base_price/chunk_type`，支持结构化过滤。

**三路并行召回**

```
用户 Query
    │
    ├─① 向量检索（Qdrant）：语义相似度，召回 top_k×2 个 chunk
    │   Query → Doubao-embedding-vision → 2048 维向量
    │
    ├─② BM25 检索（rank-bm25）：关键词精确匹配，召回 top_k×2 个 chunk
    │   Query → jieba 分词 → 同义词扩展（45+ 词典）→ IDF 打分
    │
    └─③ 结构化过滤：从 Query 中解析价格范围/类目等约束
        直接作用于 Qdrant metadata filter，预过滤候选集
```

**RRF（Reciprocal Rank Fusion）融合**

向量分数（0~1 余弦相似度）与 BM25 分数（无上界）量纲不同，无法直接加权。RRF 只依赖排名，公式：

```
score(d) = Σ 1 / (k + rank_i(d))，k=60
```

不需要调权重超参，对两路排名直接融合，最终取 top_k 候选商品。

**BGE Cross-Encoder 精排**

RRF 融合后仍是 chunk 级结果，需聚合到商品级再精排：

1. 按 `product_id` 聚合：同一商品的多个 chunk 取最高分，保留命中 chunk
2. 构造商品代表文本：`"商品:{标题}\n{最高分 chunk 内容}"`
3. 送入本地 `bge-reranker-base`（Cross-Encoder），与 Query 交互打分
4. 按精排分数重新排列，取 top_k 商品

BGE 模型不可用时，自动降级到 Doubao API 评分，确保服务可用性。

**防幻觉设计**

对比、推荐、追问场景中，商品的价格、标题、SKU、图片等结构化数据**全部直接从数据库查询**，LLM 只生成文字说明。System Prompt 明确约束：

- 严格基于检索到的商品资料回答
- 对比表中资料未提及的属性填"—"，绝不臆造
- 不编造优惠、库存、成分等未经核实的信息

### 10.3 多模态能力

**拍照找货**

```
端侧拍照/相册选图
    → 等比缩放（800px 上限）+ JPEG 压缩（quality=80）
    → Base64 编码
    → 后端 Doubao-embedding-vision 图像向量化
    → 向量检索 product_images collection（独立图像索引）
    → 返回视觉相似商品
```

Doubao-embedding-vision 将文本和图像映射到**同一 2048 维向量空间**，图搜和文搜结果可直接关联商品 ID，无需额外跨模态映射。

**语音输入（STT）**

集成系统键盘语音输入，Android 各主流机型（包括国产定制系统）均原生支持，无需引入第三方 ASR SDK，零额外依赖，语音转文字后进入与普通文字消息完全相同的处理链路。

**TTS 语音播报**

流式回复结束后，自动将 AI 文字内容通过 Android `TextToSpeech` 朗读。去除 Markdown 符号避免"星号加粗星号"等噪音播报；记录上次播报内容防止加购/对比等无新文本轮次重复朗读。

### 10.4 客户端流式渲染

`SseClient` 基于 OkHttp EventSource 将 SSE 回调桥接为 Kotlin `Flow`，`ChatViewModel` 订阅并按事件类型分发：

- `text_delta`：追加到当前流式气泡，触发滚动跟随
- `thinking/tool_progress`：替换顶部状态提示（"正在思考..."）
- `product_card_list`：锁定流式文字，追加横滑商品卡片组件
- `comparison_table`：渲染可左右滑动的对比表格，支持点击列跳转商品详情
- `clarification`：渲染流式选项按钮（FlowRow 自适应布局）
- `cart_update`：更新购物车角标 + Toast 提示
- `done`：结束流式状态，触发 TTS 播报

历史会话重新打开时，服务端返回 blocks 字段，客户端按相同事件类型重建富消息，无需重渲染。

---

## 11. 亮点与创新

### 亮点一：工程化抗幻觉——"数据-文本分离" + 状态机驱动

**与同类方案的差异：**

同类方案通常让 LLM 一步生成含价格、参数、推荐理由的完整回复，容易产生编造信息。

RAGent 将回复内容严格分为两类：

- **结构化数据**（价格、SKU、标题、图片）：全部从数据库直接查询，LLM 无法干预
- **自然语言内容**（推荐理由、对比分析、场景说明）：LLM 基于已核实的资料生成，System Prompt 明确禁止编造

对比场景尤为典型：维度提取用 LLM，但每个维度值必须来自商品原文，缺失填"—"不臆造。此外，Master Agent 采用**规则快路由优先**，常见电商意图秒级判定无需调用 LLM，只有语义真正模糊时才用模型兜底，从路由层就降低了幻觉风险。

### 亮点二：RRF 混合检索 + 商品级 Cross-Encoder 精排——两级精度提升

**与同类方案的差异：**

同类 RAG 方案常见问题：单一向量检索对精确关键词不敏感；向量 + BM25 直接加权需要手动调参，量纲不统一。

RAGent 的两级精度设计：

**第一级（召回）：** 向量 + BM25 两路并行，用 **RRF 算法**融合。RRF 只依赖排名而非分数值，从根本上规避了量纲差异，无需调权重超参数。BM25 层还引入同义词扩展（45+ 词条），覆盖"洗面奶/洁面乳"等中文变体。

**第二级（精排）：** RRF 融合后，chunk 聚合到商品级，由本地 **BGE Cross-Encoder** 对"Query + 商品代表文本"做交互式打分，召回精度相比双塔模型显著提升。BGE 不可用时自动降级豆包 API，保障服务稳定性。

### 亮点三：全加分项深度落地——从多模态到业务闭环全三档实现

项目对需求文档中 **4.1 ～ 4.3 三大加分项均完成了三档（⭐⭐⭐）**：

| 加分项 | 完成情况 |
|--------|---------|
| 4.1 购物车与下单 | 对话加购⭐ + 购物车改量/删品⭐⭐ + 下单确认填写收货信息⭐⭐⭐ |
| 4.2 多模态交互 | 语音输入⭐ + TTS 语音播报⭐⭐ + 拍照找货（VLM 视觉向量）⭐⭐⭐ |
| 4.3 对话智能 | 多轮上下文记忆⭐ + 反选排除否定语义⭐⭐ + 多商品对比表⭐⭐⭐ |
| 4.4 工程质量 | Redis 检索缓存⭐ |

各项均为端到端落地，非单独功能堆砌：拍照找货与对话搜索复用同一 SSE 事件链路；购物车状态与 Agent 编排深度联动；对比表格数据直接来自 RAG 检索资料，与防幻觉机制一体化设计。
