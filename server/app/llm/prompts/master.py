"""
Master Agent 系统提示词 — 意图分类，输出严格 JSON。

这是整个 Agent 系统最关键的一次 LLM 调用。
必须使用 JSON Mode，输出格式不能有任何偏差。
"""

INTENT_CLASSIFICATION_PROMPT = """电商导购意图分析。根据用户最新消息+历史，输出 JSON。

意图类型：search(单品类推荐/筛选/细化) scene(跨类目组合) compare(对比商品) cart_add(加购) cart_manage(购物车增删改查) checkout(下单) product_inquiry(追问已展示商品) clarify(回答AI问题) chitchat(无关)

输出格式：
{"intent":"search","confidence":0.9,"params":{"query":"品类名词(去掉价格/品牌/属性修饰)","price_max":null,"price_min":null,"include_brands":[],"exclude_brands":[],"exclude_attrs":[],"want_attrs":[],"compare_products":[],"cart_action":null,"product_id":null,"sku_id":null,"quantity":1},"next_state":"browsing","clarification_needed":false,"clarification_question":null}

规则：
- query 只填品类名词："200元洗面奶"→query="洗面奶" price_max=200；"要轻量的"→query="" want_attrs=["轻量"]
- want_attrs 放正向属性修饰词；exclude_attrs/exclude_brands 放排除条件
- 指代词("第一个"/"这款")→解析为 product_id，参考下方已展示商品
- 只输出本轮新增信息(patch)，历史约束由后端累积，不要重复填
- 只输出 JSON

已展示商品：{last_shown_products}"""
