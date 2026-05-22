# Phase 1 总结 — 最小闭环

完成时间：2026-05-22

## 完成内容

### 数据层
- 加载 100 条商品 JSON（4 类目 × 25 条）
- 每条商品切分为多个 chunk：base / description / faq / review
- 共切出 **1092 个 chunk**
- 调用 Doubao-embedding-vision 批量向量化存入 Chroma

### 服务层
- FastAPI 启动，商品数据加载进内存仓库（product_repo）
- 商品图片通过 `/static/images/` 静态服务对外暴露
- SSE 流式接口 `POST /api/v1/chat/stream` 跑通

### 链路验证
```
用户输入 → embedding → Chroma 向量检索 → 商品聚合
→ product_card 事件推送（字段从 product_repo 取，不经模型）
→ 拼 Prompt → 豆包流式生成 → text_delta 逐 token 推送
→ done 事件
```

## 已验证接口
- `POST /api/v1/sessions` — 创建会话 ✅
- `POST /api/v1/chat/stream` — 流式对话 ✅
- `GET /static/images/` — 商品图片 ✅
- `GET /health` — 健康检查 ✅

## 防幻觉措施（已落地）
- 商品卡片字段（title / price / image_url）全部从 product_repo 取，**不经过模型**
- System Prompt 明确禁止模型复述具体价格
- 模型只负责生成推荐理由文案

## 已知问题（Phase 2 解决）
- 纯向量检索精度不足：用户问"洗面奶"，召回结果混入防晒、卸妆油等相关但不准确的品类
- 原因：向量检索只看语义相似度，无法精确匹配品类关键词
- 解决方案：Phase 2 加 BM25 召回 + Reranker 精排

## 技术坑记录

| 问题 | 原因 | 解决 |
|---|---|---|
| embedding API 400 错误 | doubao-embedding-vision 不支持标准 OpenAI /embeddings 接口 | 改用 /embeddings/multimodal + httpx 直连 |
| `KeyError: 0` | 响应 data["data"] 是 dict 不是 list | 改为 data["data"]["embedding"] |
| pip 下载慢 | 默认 PyPI 源在国外 | 换清华镜像 -i https://pypi.tuna.tsinghua.edu.cn/simple |
| Chroma telemetry 警告 | 版本兼容问题 | 不影响功能，忽略 |

## Phase 2 计划
1. BM25 召回（rank_bm25）
2. 混合检索 + RRF 融合
3. Cross-Encoder Reranker 精排
4. Query 改写（多轮对话）
5. 评估脚本 Recall@K
