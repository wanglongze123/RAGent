# 系统架构总览

> 后端：基于 RAG 的多模态电商智能导购 Agent
> 技术栈：Python 3.13 / FastAPI / SSE / Chroma / SQLite / Doubao-Seed-2.0-lite
> 模型：Doubao-embedding-vision（文本 + 图片同空间）
> 商品集：100 件，分 4 个一级类目（美妆护肤 / 数码电子 / 服饰运动 / 食品生活）

---

## 一、分层架构图

```
┌────────────────────────────────────────────────────────────────┐
│  Client (iOS/Android)                                           │
└────────────────────────────────────────────────────────────────┘
                            │ HTTPS + SSE
┌──────────────────────────▼─────────────────────────────────────┐
│  ① API 层  (FastAPI)                                            │
│    api/chat.py     POST /sessions / chat/stream (SSE)            │
│    main.py         应用入口、启动加载、CORS、static/               │
└──────────────────────────┬─────────────────────────────────────┘
                           │
┌──────────────────────────▼─────────────────────────────────────┐
│  ② Agent 编排层 (控制平面)                                       │
│                                                                  │
│   ┌───────────────────────────────────────────────────────┐    │
│   │ MasterAgent (master_agent.py)                         │    │
│   │   • 意图分类（规则快速通道 + LLM 兜底）                  │    │
│   │   • 状态机路由（state_machine.py）                      │    │
│   │   • 上下文管理（last_shown / 消息历史 / 状态持久化）      │    │
│   └───────────────────────────────────────────────────────┘    │
│              │                                                   │
│              ▼ dispatch                                          │
│   ┌─────────┬─────────┬─────────┬─────────┬─────────┐          │
│   │ search  │ compare │ scene   │  cart   │ order   │          │
│   │ agent   │ agent   │ agent   │ agent   │ agent   │          │
│   └────┬────┴────┬────┴────┬────┴────┬────┴────┬────┘          │
│        │         │         │         │         │                 │
│        └─────────┴─────────┴─────────┴─────────┘                 │
│                          │                                       │
│   ┌──────────────────────▼────────────────────────────────┐    │
│   │ Middleware (middleware.py) — LLM 调用唯一入口          │    │
│   │   • 注入对应 system prompt                              │    │
│   │   • JSON Mode 抽取 + 校验                               │    │
│   │   • 工具白名单（工具空间隔离）                           │    │
│   │   • 链路追踪日志                                         │    │
│   └─────────────────────────┬─────────────────────────────┘    │
└─────────────────────────────┼───────────────────────────────────┘
                              │
        ┌─────────────────────┴─────────────────────┐
        │                                           │
┌───────▼─────────────────┐         ┌───────────────▼────────────┐
│  ③ RAG 检索层             │         │  ④ Model 层                  │
│                          │         │                            │
│  hybrid_retriever        │         │  llm/client.py             │
│   ├─ Chroma (向量召回)    │         │   • Doubao Chat (流式)     │
│   ├─ BM25  (词法召回)     │         │   • Doubao Chat (JSON)     │
│   ├─ RRF 融合            │         │   • embed_text             │
│   ├─ BGE Reranker        │         │   • embed_image            │
│   └─ retrieve_by_image   │         │  llm/prompts/*.py          │
│                          │         │   • 6 套 system prompt     │
│  query_expander.py       │         │     master / search /      │
│   • 同义词扩展            │         │     compare / cart /       │
│                          │         │     order / scene          │
└──────────┬───────────────┘         └────────────────────────────┘
           │
┌──────────▼─────────────────────────────────────────────────────┐
│  ⑤ 数据层                                                        │
│                                                                  │
│  product_repo.py   100 商品 JSON 全量内存 (O(1) 查询)             │
│                    {region: [brand]} 启动时聚合（数据驱动）         │
│                                                                  │
│  vector_store.py   抽象接口；Chroma 实现                          │
│                    collection: products / product_images           │
│                                                                  │
│  relational.py     SQLite                                         │
│                    sessions / messages / cart_items / orders /    │
│                    order_items                                    │
└──────────────────────────────────────────────────────────────────┘
```

---

## 二、各层设计与功能

### ① API 层（FastAPI）

| 文件 | 职责 |
|---|---|
| `main.py` | 启动入口：依次加载 SQLite 表、商品库、向量库、BM25 索引、BGE reranker；挂载 `/static/images/` 给客户端访问商品图 |
| `api/chat.py` | 两个端点：`POST /api/v1/sessions` 创建会话；`POST /api/v1/chat/stream` SSE 流式对话 |

**SSE 事件 schema**（`models/events.py`）：

```
thinking          • 立即推送，告诉用户"在想了"
tool_progress     • 检索/规划阶段进度提示
text_delta        • 流式文本 token
product_card      • 商品卡（搜索/场景/图搜命中后逐个推）
product_card_list • 批量商品卡（少用）
comparison_table  • 对比维度表（compare_agent）
cart_update       • 购物车数量变化通知（前端更新角标）
clarification     • 反问用户（含 options 选项）
image_searching   • 图搜专属进度
error             • 业务错误
done              • 流结束 + 当前 agent_state
```

### ② Agent 编排层（核心）

#### MasterAgent

**职责（只做控制平面）**：
1. 意图分类
2. 状态机路由
3. 上下文管理

**意图分类双路径**（`_classify_intent`）：
- **快路径**（`_quick_classify` 规则）：~10 ms，覆盖加购 / 删除 / 改数量 / 对比 / 下单 / 推荐 / 场景等模板化消息
- **慢路径**（LLM JSON Mode）：5-15s，仅模糊场景兜底

**状态机**（`state_machine.py`）：

```
状态                          允许的子 Agent
──────────────────────────────────────────────────
BROWSING                      search, compare, scene
COMPARING                     search, compare, scene
CART_MANAGEMENT               cart, search, scene
CHECKOUT                      order  ← 严格限定，防止中途跑偏
```

**转移规则**（部分）：

```
(BROWSING,        cart_add)         → CART_MANAGEMENT
(BROWSING,        compare)          → COMPARING
(BROWSING,        checkout)         → CHECKOUT
(COMPARING,       search)           → BROWSING
(COMPARING,       cart_add)         → CART_MANAGEMENT
(CART_MANAGEMENT, search)           → BROWSING
(CART_MANAGEMENT, checkout)         → CHECKOUT
(CHECKOUT,        order_confirmed)  → BROWSING
未列出的组合保持原状态
```

**上下文管理**：
- 消息历史：`messages` 表，每轮 add user + assistant 两条
- `last_shown_products`：商品卡推完即时持久化（不等流结束），客户端中断也不丢数据
- `agent_state` 写权：master 只在真正发生状态机转移时写；其他时候让 sub-agent 自己写（避免覆盖 sub-agent 设的 state）

#### Sub-Agents（5 个）

| Agent | 输入 | 输出 | 关键设计 |
|---|---|---|---|
| **search** | 文本 query 或 image_base64 | 5 张商品卡 + 流式推荐理由 | 卡片字段全从 product_repo 取（防 LLM 幻觉）；图搜分支调 `retrieve_by_image` |
| **compare** | 商品名 / `last_shown` 引用 | 对比表 + 推荐理由 | 4 步法：商品识别 → asyncio.gather 并行检索 → LLM JSON 提取维度 → 表格组装 |
| **scene** | 场景描述 | 多主题商品卡 + 场景搭配文案 | LLM 拆解 2-4 主题 → asyncio.gather 并行检索 → 跨主题去重 |
| **cart** | action（add/remove/update_qty/view/clear） | cart_update 事件 + 确认文字 | 强结构化操作；多 SKU 时弹 clarification 等待用户回；`_resolve_cart_item_id` 把 product_id / 位置词 / 唯一项映射到 cart_item_id |
| **order** | message（按字段判断阶段） | text_delta（确认 / 询问下一字段 / 提交结果） | order_state 持久化每步状态；任何阶段说"算了"全局取消；提交成功后写 `agent_state="browsing"` 退出 checkout |

#### Middleware (`middleware.py`)

**所有 LLM 调用唯一入口**。业务代码不直接调 `llm_client`。

```python
# 业务代码
await middleware.chat(agent_name="master", user_messages=[...], json_mode=True)
await middleware.chat_stream(agent_name="search", prompt_vars={"context": ...})
```

- `AGENT_PROMPTS` 注册每个 agent_name 对应的 system prompt 模板
- `AGENT_TOOLS` 工具白名单（cart 才有 `cart_add` / `cart_remove` / `cart_update_quantity`）
- JSON Mode 兼容（Doubao-Seed-2.0-lite 不支持 `response_format`，改用 prompt 强制）

### ③ RAG 检索层

#### 文本检索管道

```
query
  └─ query_expander          同义词扩展（jieba + 词典）
  └─ parse_query             结构化抽取（价格区间）
       ├─ semantic 部分 → 向量检索（Doubao multimodal embedding → Chroma）
       └─ where filter → metadata 过滤
  └─ BM25 倒排                同一 query 词法召回
  └─ RRF 融合                 score(d) = Σ 1/(k+rank(d))，绕开量纲
  └─ 商品级聚合                chunks → products，取最高分 chunk 代表
  └─ BGE Reranker            商品级精排（"标题 + 最相关 chunk"作 representation）
  └─ top-K
```

**Chunking 策略**（`scripts/build_index.py`）：
- 1 chunk：base（标题 + 品牌 + 类目 + 价格） — 用户用品牌名 + 品类搜索时这条最易命中
- 1 chunk：description（marketing_description） — 检索"功效/适用人群/卖点"
- N chunks：每条 official_faq 独立 — 用户问具体问题时高质量命中
- N chunks：每条 user_review 独立 — 真实体验问题命中

**为什么 Reranker 在商品级而非 chunk 级**：
chunk 级会被"提到油皮的防晒品"干扰；商品级用"标题 + 最高分 chunk"代表商品，让 BGE 看清楚商品是什么。

#### 图搜独立通道

```
image_base64
  └─ embed_image             Doubao multimodal 接口
  └─ Chroma product_images   单路向量召回
                             不走 BM25（图片没文本）
                             不走 reranker（BGE 是文本）
  └─ top-K product_id
```

**关键**：Doubao 文本和图片走同一 multimodal endpoint → 文本向量与图片向量在**同一空间**，可直接互查。

### ④ Model 层

| 接口 | Doubao endpoint |
|---|---|
| `chat_stream` / `chat` | `chat/completions`（Doubao-Seed-2.0-lite） |
| `embed_text` / `embed_image` | `/embeddings/multimodal`（Doubao-embedding-vision，**文本 + 图片同空间**） |

`prompts/` 下 6 个 system prompt 模板，每个 sub-agent 一份；scene 有两份（planning JSON + reasoning streaming）。

### ⑤ 数据层

| 仓库 | 数据 | 选型 |
|---|---|---|
| `product_repo` | 100 商品全量内存（dict[id, Product]） | dict — O(1) 查询；启动时聚合 `{region: [brand,...]}` |
| `vector_store` | 文本 chunks + 图片向量（两个 collection） | Chroma 持久化到 `chroma_db/` |
| `relational` | sessions / messages / cart_items / orders / order_items | SQLite，生产换 RDS MySQL 只改连接串 |

**SQLite 表**：

```sql
sessions:        session_id PK / agent_state / last_shown_products / order_state / created_at / updated_at
messages:        id PK / session_id / role / content / created_at
cart_items:      cart_item_id PK / session_id / product_id / sku_id / title / image_url /
                 sku_props / unit_price / quantity / created_at
orders:          order_id PK / session_id / status / receiver_* / total_price / created_at
order_items:     id PK / order_id / product_id / sku_id / title / quantity / unit_price
```

价格 / 标题在加购时**快照到 cart_items**，防止商品改价后购物车展示出错。

---

## 三、数据驱动的关键设计

### 商品 JSON 自带 region

每个 `ecommerce_agent_dataset/<品类>/data/p_xxx.json` 含字段：

```json
{
  "product_id": "p_beauty_018",
  "title": "...",
  "brand": "The Ordinary",
  "category": "美妆护肤",
  "sub_category": "精华",
  "base_price": 59,
  "image_path": "...",
  "region": "欧美",          ← 这一个字段驱动所有"不要日系"类硬过滤
  "skus": [...],
  "rag_knowledge": {...}
}
```

### product_repo 启动时聚合

```python
def _build_region_index(self):
    by_region: dict[str, set[str]] = defaultdict(set)
    for p in self._products.values():
        if p.region and p.brand:
            by_region[p.region].add(p.brand)
    self._brands_by_region = {r: sorted(bs) for r, bs in by_region.items()}
```

`product_repo.brands_in_region("日系")` → `["SK-II", "优衣库", "安热沙", "日清", "珊珂", "芳珂", "资生堂"]`

### 别名表（用户口语统一）

`_REGION_ALIASES = {"国货": "国产", "美系": "欧美", "欧系": "欧美", ...}`

用户说"国货"自动等同于"国产"。

### 扩展场景

加新品牌：在 product JSON 写 `"region": "日系"` → 用户说"不要日系"立即把它过滤掉。**零代码改动**。

加新地域：在 product JSON 写 `"region": "中东"` → 启动时 product_repo 自动收录"中东"为 region 关键词，用户说"不要中东"立即生效。**零代码改动**。

### Backfill 工具

```bash
python -m scripts.backfill_region          # 增量：只补缺 region 的商品
python -m scripts.backfill_region --force  # 强制全量重打
```

LLM 一次调用对所有唯一品牌打标，写回 product JSON。

---

## 四、当前进展

### 项目要求 §1.2 用户场景

| 难度 | 场景 | 状态 |
|---|---|---|
| 基础 | 单轮模糊推荐 | ✅ |
| 基础 | 条件筛选（价格） | ✅ |
| 进阶 | 多轮追问 | ✅ |
| 进阶 | 对比决策 | ✅ |
| 进阶 | Agent 主动反问 | ✅ |
| 高级 | 反选 / 排除（含数据驱动 region） | ✅ |
| 高级 | **场景化组合推荐**（scene_agent） | ✅ |
| 高级 | 购物车 + 下单 | ✅ |
| 高级 | 拍照找货 | ✅ |

**9/9 全过**

### 加分项 §4

| 类别 | ⭐ | ⭐⭐ | ⭐⭐⭐ |
|---|---|---|---|
| 4.1 业务闭环 | ✅ 加购 | ✅ 删/改/清空 | ✅ 下单完整流程 |
| 4.2 多模态 | ❌ ASR | ❌ TTS | ✅ 拍照找货 |
| 4.3 对话/RAG | ✅ 多轮记忆 | ✅ 反选 | ✅ 对比 |
| 4.4 工程质量 | ❌ 缓存 | ✅ **首 Token <20ms**（要求 <1s） | — 客户端项 |

---

## 五、关键工程修复回顾

| # | 问题 | 修复 |
|---|---|---|
| 1 | 客户端中断流时 `last_shown_products` 不持久化 → 下一轮加购报"未找到" | master 改为商品卡推完即时持久化 |
| 2 | CHECKOUT 态下"张三"被 master 当 clarify 拦截 → order_agent 收不到 | CHECKOUT 强制 `needs_clarify=False` |
| 3 | LLM 幻觉 `product_id="P001"` 不能解析 | `_resolve_position_reference` 三层兜底（用户原话位置词 / LLM 数字位置词 / fallback last_shown[0]） |
| 4 | `_BRAND_CATEGORY_MAP` 硬编码字典扩商品就过期 | 改成数据驱动 region（见上） |
| 5 | order_agent 在收姓名阶段说"算了"被存为 receiver_name="算了" | run() 顶层全局 cancel 检测 |
| 6 | 提交订单后 agent_state 卡在 checkout，下一轮请求被困死 | order_agent 提交成功后写 browsing；master 在无状态转移时不覆盖 sub-agent 设的 state |
| 7 | compare 后 `last_shown` 是空，加购"第一个"读不到 | `_extract_products_from_event` 也提取 `comparison_table` 的商品 |
| 8 | cart_agent 收到 product_id 不会找 cart_item_id → 删除 / 改数量跑不动 | 新增 `_resolve_cart_item_id` 函数 |
| 9 | LLM 漏识别"不要日系" / "无酒精" | master `_enrich_filters_from_message` 关键词兜底 |
| 10 | **首 Token 5-15s** 远超项目要求 | 规则快速通道 → **<20ms** |
| 11 | 同 SKU 累加：`cart_total_count` 不对 | DB upsert 累加 quantity |
| 12 | 空购物车下单后状态卡 checkout | order_agent 空车主动写 browsing |

---

## 六、测试覆盖

`docs/phase3_e2e_test.md` 共 **7 个测试阶段、30+ 端到端场景**：

| 阶段 | 内容 | 子项数 |
|---|---|---|
| 0 主路径 | 搜索 / 多轮 / 加购 / 下单完整链路 / 截断恢复 / 对比 | 9 |
| 1 Cart 完整 | 累加 / 改数量 / 删除 / 清空 / 中途取消 | 5 |
| 2 Order 边界 | 空车下单 / 手机号格式校验 | 2 |
| 3 Search 硬过滤 | 不要日系 / 不要国货别名 / 不要韩系（数据驱动证明） / 价格区间 / 属性排除 / 零结果 | 4+ |
| 4 状态机边界 | CART→search 转 BROWSING / COMPARE→cart_add 转 cart_management / 空 session 加购 | 3 |
| 5 多模态图搜 | 同图召回 / 食品图同品类 / 服饰图同品类 | 3 |
| 6 首 Token 优化 | 9 步链路全部 <20ms | 9 |
| 7 场景化组合 | 三亚度假 / 露营装备 / 送礼 | 3 |

**全部通过。**

---

## 七、git 提交历史（dev/server 分支）

```
2a571a2  feat: scene_agent — cross-category combo recommendation     ← HEAD
c71e6b0  perf: rule-based fast path (first-token <20ms)
104db86  feat: phase 5 multimodal image search
f10bd02  feat: data-driven brand region for hard filters
d3d526c  fix: cart/order/search edge cases + e2e test doc
e2e2de5  fix: phase3 routing & persistence bugs
96e4748  fix: phase3 bugs - state check, position resolution, ...
5cff754  feat: phase3 step9 - order agent
0f44398  feat: phase3 step8 - cart agent
4ddf289  feat: phase3 step7 - compare agent
795f2db  feat: phase3 step6 - search agent
09eb771  feat: phase3 step5 - master agent + sub-agent stubs
4e63766  feat: phase3 step4 - tool middleware
3852e43  feat: phase3 step3 - system prompts for all agents
e1216d6  feat: phase3 step2 - agent state machine
13067f0  feat: phase3 step1 - SQLite data layer
```

当前领先 `origin/dev/server` 4 个 commit（待 push）。

---

## 八、待办（不阻塞交付）

| 项 | 优先级 | 工作量 |
|---|---|---|
| README 部署文档 | 中 | 1 h |
| 自动化测试（pytest） | 中 | 2-3 h |
| 4.1-⭐ 热门查询缓存 | 低 | 1 h |
| 4.2 ASR / TTS | 低 | 后端 1-2 h，端侧另算 |
| 服务稳定性（限流 / 熔断 / 降级） | 低 | 2-3 h |

后端**初版完成度足以进入答辩**。

---

## 九、可演化性说明

设计目标是"中型电商档"（1K – 1M 商品）单机能跑、可演化。下面是各层在不同档位下的状态：

| 层 | 当前实现 | 中型电商档 | 超大规模档 |
|---|---|---|---|
| API 层 | 单 FastAPI | 加 nginx + 多 worker | API gateway + 限流 + 灰度 |
| Agent 编排层 | Python in-process | 同前（核心逻辑无关规模） | 拆微服务（Agent 控制平面 / 子 Agent 数据平面） |
| 中间件 LLM | Doubao API | 同前 + 缓存 | 多模型路由 + 降级 |
| RAG 层 | Chroma + BM25 | Milvus / VikingDB；BM25 入 ES | 同前 + 多副本 + GPU rerank |
| 数据层 | 内存 dict + SQLite | Redis 缓存 + RDS MySQL | 商品中心微服务 + CDC + Kafka 事件流 |

**代码复用率**：换实现不换接口，业务逻辑（master / sub_agents / state_machine）零改动。
