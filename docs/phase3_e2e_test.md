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

### 3.1 品牌排除（日系类型映射）
- **输入**：`推荐美妆护肤品，不要日系的`
- **修复点 1**：master 加 `_enrich_filters_from_message`，扫用户原话关键词（"不要日系"/"不喜欢日系"/"避开日系"）补 exclude_brands。LLM 经常漏识别
- **修复点 2**：`_BRAND_CATEGORY_MAP["日系"]` 补全 SK-II
- **结果**：5 张返回都是欧美品牌（雅诗兰黛/玉兰油/兰蔻×2/科颜氏），无日系 ✓

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

## 阶段 5 ⏳ 多模态图搜（链路未接通）

- ✅ `llm_client.embed_image`（Doubao 多模态 endpoint）已实现
- ✅ chat.py / master / search_agent 接了 `image_base64` 入参
- ❌ **search_agent 拿到 image_base64 后没用**：没调 embed_image，没接进 retriever
- ❌ hybrid_retriever / vector_store 没有图搜入口

需要补：search_agent 在 image_base64 非空时改走 embed_image → vector_store 图搜分支；vector_store 需要图片向量索引或同库混合查询。
