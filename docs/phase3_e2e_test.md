# Phase 3 端到端测试记录

> 测试方式：本地 uvicorn 启动 server，curl 模拟客户端，逐步验证 SSE 事件流 + SQLite 持久化结果。
> 测试时间：2026-05-23
> 涉及修复：commit `e2e2de5 fix: phase3 routing & persistence bugs`

---

## 阶段 0 ✅ 已通过 — 主路径

### 0.1 完整搜索

- **输入**：`推荐一款适合油皮的洗面奶`
- **期望**：5 张 product_card + 流式推荐理由
- **结果**：
  - SSE 事件：`thinking` → `tool_progress(hybrid_search)` → 5 个 `product_card` → 多个 `text_delta` → `done(browsing)`
  - DB：`sessions.last_shown_products` 含 5 个商品 id（即时持久化生效）

### 0.2 多轮上下文（条件细化）

- **输入**：`推荐一款适合油皮的洗面奶` → `100元以内`
- **期望**：第二轮承接上轮品类 + 价格过滤
- **结果**：第二轮返回 4 张 product_card，价格全部 ≤ 100 元

### 0.3 加购第一个 → 选规格 → 入库

- **输入**：搜索后 → `把第一个加入购物车` → `120g 标准装`
- **期望**：弹 SKU clarification → 收到选项后入库
- **结果**：
  - 第一轮：`event: clarification` 列出两个规格选项
  - 第二轮：`event: cart_update`（cart_total_count=1, cart_total_price=52.0）
  - DB：`cart_items` 表写入 `p_beauty_011 / s_p_beauty_011_1 / 120g 标准装 / qty=1`
- **修复点**：LLM 幻觉 `product_id="P001"` 时三层兜底兜回 last_shown[0]

### 0.4 查看购物车

- **输入**：`查看购物车`
- **结果**：text_delta 输出 `1. 珊珂洗颜专科…（规格:120g 标准装） × 1  小计 ¥52.0\n\n合计：¥52.0`

### 0.5 完整下单链路

| 步骤 | 输入 | 响应关键字 | DB 状态变化 |
|---|---|---|---|
| 我要下单 | `我要下单` | "为您汇总购物车内容…确认下单吗？" | order_state.\_cart_shown=true，agent_state→checkout |
| 确认 | `确认` | "好的！请问收货人姓名是？" | order_state.confirmed_cart=true |
| 姓名 | `张三` | "收货人：张三 ✓\n请问联系手机号是？" | order_state.receiver_name="张三" |
| 手机号 | `13800138000` | "手机号：138****8000 ✓\n请问收货地址是？" | order_state.receiver_phone="13800138000" |
| 地址 | `北京市朝阳区建国路1号` | "收货信息确认：…信息无误，确认提交订单吗？" | order_state.receiver_address |
| 确认提交 | `确认提交` | "🎉 订单提交成功！订单号：ord_xxx\n总金额：¥52.0" | orders 表写入，cart_items 清空，order_state 清空，**agent_state→browsing** |

- **修复点**：CHECKOUT 态强制 `needs_clarify=False`，否则"张三""13800138000"会被 master 当作 clarify 拦截，order_agent 收不到。

### 0.6 下单后回浏览（不再卡死）

- **输入**：完成下单后 → `再推荐一款防晒`
- **结果**：返回 4 张 product_card，agent_state=browsing
- **修复点**：order_agent 提交成功后写 `agent_state="browsing"`；master 在 `next_state == current_state` 时不再覆盖 sub-agent 的 state 写入。

### 0.7 截断恢复（客户端中断）

- **场景**：搜索 SSE 流被 `head -30` 提前关掉（模拟前端 unmount / 网络断），再发起加购
- **修复前**：last_shown_products 留在 DB 里是 `[]`，下一轮加购报"未找到该商品"
- **修复后**：DB 即时持久化 5 个商品 id，下一轮 `把第一个加入购物车` 正确弹 SKU clarification
- **修复点**：`master_agent.run()` 改成"每来一个 product_card 事件立即 update_session_state"，不再等流结束才写。

### 0.8 Compare：直接报名

- **输入**：`对比雅诗兰黛小棕瓶和SK-II神仙水`
- **结果**：
  - `comparison_table` 含两个商品（p_beauty_001 ¥720 + p_beauty_003 ¥1690）
  - 4 个对比维度：核心成分 / 主打功效 / 适用人群 / 使用方法
  - agent_state→comparing

### 0.9 Compare：last_shown 引用

- **输入**：`推荐两款防晒` → `对比这前两个`
- **结果**：从 last_shown 取 [0,1]：安热沙金灿倍护 + 理肤泉特护清盈，正确生成对比表

---

## 阶段 1 ✅ Cart 完整能力

### 1.1 同 SKU 累加
- **输入**：搜防晒 → 加购第一个 → 50ml清盈型 → 再加一个第一个 → 同 SKU
- **结果**：DB cart_items.quantity 1→2，cart_total_count=2，total_price=536.0 ✓

### 1.2 修改数量
- **输入**：`把购物车里的理肤泉数量改成5件`
- **修复点**：cart_agent 加 `_resolve_cart_item_id` 函数：product_id → cart_item_id；用户原话位置词；唯一项兜底。LLM 通常只能给 product_id 不能给 cart_item_id
- **结果**：DB quantity 1→5，cart_total_count=5 ✓

### 1.3 删除单项（位置词）
- **输入**：购物车有 2 件 → `删除第二个`
- **结果**：cart_update action=remove，DB 从 2 条变 1 条 ✓

### 1.4 清空购物车
- **输入**：`清空购物车`
- **结果**：cart_update action=remove + total=0 + DB cart_items 数=0 ✓

### 1.5 下单中途取消
- **输入**：进入收姓名阶段 → `算了`
- **修复前**：order_agent._handle_collect_name 没有 cancel 检测，"算了" 被存为 receiver_name
- **修复点**：order_agent.run() 顶层加全局 cancel 检测，命中 cancel 关键词直接 clear_order_state + agent_state→cart_management
- **结果**：DB agent_state=cart_management，order_state={}，购物车保留 ✓

## 阶段 2 ✅ Order 边界

### 2.1 空购物车下单
- **输入**：空 session → `我要下单`
- **修复点**：购物车空时 order_agent 主动 update_session_state(agent_state="browsing") + clear_order_state，否则 state 卡在 checkout
- **结果**：text_delta="您的购物车是空的…"，state→browsing ✓

### 2.2 手机号格式校验
- 错号 `1234`：拒 ✓
- 错前缀 `12345678901`（11位但开头不是 1[3-9]）：拒 ✓
- 合法 `13900139000`：接受，掩码显示 `139****9000` ✓

## 阶段 3 ✅ Search 硬过滤

### 3.1 品牌排除（数据驱动版）
- **输入**：`推荐美妆护肤品，不要日系的`
- **修复点 1**：master 加 `_enrich_filters_from_message`，扫用户原话关键词（"不要日系"/"不喜欢日系"/"避开日系"）补 exclude_brands
- **修复点 2 — 关键架构改造**：弃掉 `_BRAND_CATEGORY_MAP` 硬编码字典，改成数据驱动：
  1. `Product` 模型加 `region` 字段（"日系"/"欧美"/"国产"/"韩系"/"东南亚"）
  2. `scripts/backfill_region.py` 用一次 LLM 调用为存量 100 商品打地域标签后写回 JSON
  3. `product_repo` 启动时聚合 `{region: [brand,...]}`，暴露 `regions()` / `brands_in_region()` / `all_region_keywords()`
  4. `_filter_brands` 改调 `product_repo.brands_in_region("日系")` 动态拿
  5. master `_enrich_filters_from_message` 也从 `product_repo.all_region_keywords()` 动态拿
  6. 别名（"国货"→"国产"，"美系"→"欧美"）由 product_repo 集中收口
- **结果**：3 个验证场景全过
  - 不要日系：返回欧美/国产，无 SK-II/资生堂/珊珂 ✓
  - 不要**国货**（别名）：返回 2 张 Apple，无国产 ✓
  - 不要**韩系**（旧硬编码字典里压根没这一类）：自动从 AHC 这个新地域品牌过滤 ✓
- **可扩展性**：以后新增任意国家品牌，只需在 product JSON 里写 `"region": "<新地域>"`，零代码修改自动生效。

### 3.2 价格区间
- **输入**：`100到500元之间的精华`
- **结果**：5/5 商品价格全部 ∈ [100,500] ✓
- master log：`price_max=500, price_min=100`

### 3.3 属性排除（部分生效）⚠️
- **输入**：`不含酒精的爽肤水`
- **结果**：master 正确写入 `exclude_attrs=['含酒精','酒精']`，但 chunk 子串过滤未必命中（兰蔻大粉水实际含酒精但 chunks 里没明确写"酒精"字样）
- **已知限制**：`_filter_attrs` 是 chunk 文本子串匹配；要做对需要结构化成分字段，超出 RAG 现有能力

### 3.4 零结果
- **输入**：`50000元以上的化妆品`
- **结果**：cards=0 + text_delta="抱歉，根据您的条件暂时没有找到合适的商品…" ✓

## 阶段 4 ✅ 状态机边界

### 4.1 CART_MANAGEMENT 态下重新搜索
- 加购完处于 cart_management → `再推荐一款防晒` → 4 张 product_card，state→browsing（按 transitions (CART_MANAGEMENT, search) → BROWSING），购物车保留 ✓

### 4.2 COMPARING 态下加购
- **修复前**：compare 只发 comparison_table 事件，master 的 `_extract_products_from_event` 只看 product_card 子串，导致 last_shown 持久化为空，下一轮加购读不到
- **修复点**：`_extract_products_from_event` 检查 `product_card OR comparison_table` 子串
- **结果**：对比后 last_shown=2 商品，`把第一个加入购物车` 弹 SKU 选择，state→cart_management ✓

### 4.3 空 session 直接加购
- 新建 session 直接 `把第一个加入购物车` → "请告诉我您想加购哪款商品？"，state→cart_management ✓

## 阶段 5 ✅ 多模态图搜

### 架构（独立 collection 路线）

- 关键观察：Doubao 的 `embed_text` 和 `embed_image` 都走 `/embeddings/multimodal`，文本向量与图片向量在**同一空间**，可以直接互查
- 路线选择：图片单独建 chroma collection `product_images`（每商品 1 个图向量），与文本 collection 隔离 —— 模态不污染、独立可调
- 数据流：
  1. `scripts/build_index.py --with-images` 把每个商品的 live 图 base64 → `embed_image` → 入 `product_images`
  2. `hybrid_retriever.retrieve_by_image(image_base64, top_k)` 单路向量召回（不走 BM25/reranker，因为图片没文本可打分）
  3. `search_agent.run()` 检测到 `image_base64` 入参 → 推 `image_searching` 事件 → 调上述方法 → 共用商品卡 + 推荐理由的下游逻辑
  4. `master_agent.run()` 检测到图直接走 search 意图，跳过 LLM 意图分类（图在文本分类里没法表达）

### 三场景测试

| 场景 | 输入图 | 期望 | 实际 Top-5 |
|---|---|---|---|
| 1 同图召回 | `p_beauty_018_live.jpg`（The Ordinary 烟酰胺精华） | 自己排第 1 | ✅ p_beauty_018 / 科颜氏精华 / 雅诗兰黛精华 / 兰蔻精华 / SK-II — 全部精华类 |
| 2 食品 | `p_food_001_live.jpg`（三顿半咖啡） | 同品类聚集 | ✅ p_food_001 / 三顿半冷萃 / 雀巢三合一 / 雀巢冻干 / 三只松鼠坚果 — 4/5 是咖啡 |
| 3 服饰 | `p_clothes_005_live.jpg`（李宁卫衣） | 同品类聚集 | ✅ p_clothes_005 / 阿迪卫衣 / Nike T恤 / 联想笔记本 / 优衣库 T恤 — 4/5 是上衣（笔记本属深色产品图相似度噪声） |

### 修复点 / 注意

- `master_agent` 在 image_base64 非空时跳过 `_classify_intent`，否则空 message 让 LLM 分意图会失败
- 推荐理由在图搜场景下用 `product.rag_knowledge.marketing_description` 作为 LLM 上下文（hit_chunks 是空的）
- 图搜走品牌硬过滤；属性过滤需要 chunk 文本，跳过
- 客户端断流时图搜路径同样会即时持久化 last_shown（沿用阶段 0.7 的修复）

### 已知限制

- Doubao 多模态 embedding 在跨品类的"产品照"场景下相似度会受**背景色/构图**影响（测试 3 的笔记本干扰）。生产可用 reranker 模型（如 jina-clip-v2）做二阶段 rerank
- `embed_image` 的 100 张索引耗时 ~50s，新商品要重跑（增量化未做）

### 工具

- `python -m scripts.build_index --images-only` 只重建图片索引（幂等 upsert）
- `python -m scripts.build_index --with-images` 文本+图片一起建
