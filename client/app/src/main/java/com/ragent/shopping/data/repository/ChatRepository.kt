package com.ragent.shopping.data.repository

import com.google.gson.Gson
import com.google.gson.JsonParser
import com.ragent.shopping.data.model.AgentState
import com.ragent.shopping.data.model.CartItem
import com.ragent.shopping.data.model.ChatMessage
import com.ragent.shopping.data.model.ChatRequest
import com.ragent.shopping.data.model.ComparisonTable
import com.ragent.shopping.data.model.CartResponse
import com.ragent.shopping.data.model.ImageSearchRequest
import com.ragent.shopping.data.model.Product
import com.ragent.shopping.data.model.SessionSummary
import com.ragent.shopping.data.model.SseEventType
import com.ragent.shopping.data.local.SessionPrefs
import com.ragent.shopping.data.remote.ApiService
import com.ragent.shopping.data.remote.NetworkConfig
import com.ragent.shopping.data.remote.SseClient
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.mapNotNull

/**
 * 对话 Repository：管理会话 ID、发起 SSE 流式对话，将原始 SSE 事件解析为 ChatMessage。
 * 使用对象单例，保证整个 App 生命周期内 sessionId 不丢失。
 */
class ChatRepository(
    private val apiService: ApiService = ApiService(),
    private val sseClient: SseClient = SseClient(),
    private val gson: Gson = Gson(),
) {
    var sessionId: String = ""
        private set

    /**
     * 确保有可用会话：内存已有则复用；否则读 DataStore 持久化的 ID（App 重启恢复）；
     * 都没有才新建并持久化。这样重启后能续上同一会话的上下文与购物车。
     */
    suspend fun ensureSession(): String {
        if (sessionId.isNotEmpty()) return sessionId
        val saved = SessionPrefs.getSessionId()
        sessionId = if (!saved.isNullOrEmpty()) {
            saved
        } else {
            apiService.createSession().sessionId.also { SessionPrefs.setSessionId(it) }
        }
        return sessionId
    }

    /** 新建会话并持久化为当前会话 */
    suspend fun newSession(): String {
        sessionId = apiService.createSession().sessionId
        SessionPrefs.setSessionId(sessionId)
        return sessionId
    }

    /** 切换到指定历史会话并持久化 */
    suspend fun switchSession(id: String): String {
        sessionId = id
        SessionPrefs.setSessionId(id)
        return id
    }

    fun resetSession() {
        sessionId = ""
    }

    /** 会话列表（抽屉用） */
    suspend fun loadSessions(): List<SessionSummary> = apiService.getSessions().sessions

    /** 当前会话购物车（切换/恢复后同步角标用） */
    suspend fun getCart(): CartResponse = apiService.getCart(sessionId)

    /**
     * 拉取并映射历史消息为 UI 消息列表。
     * assistant 消息先还原文字气泡，再按 blocks 复用 parseSseEvent 重建商品卡等富组件。
     */
    suspend fun loadHistory(id: String): List<ChatMessage> {
        val resp = apiService.getMessages(id)
        val out = mutableListOf<ChatMessage>()
        for (m in resp.messages) {
            when (m.role) {
                "user" -> if (m.content.isNotBlank()) out += ChatMessage.User(m.content)
                "assistant" -> {
                    if (m.content.isNotBlank()) {
                        out += ChatMessage.AiText(m.content, isStreaming = false)
                    }
                    for (b in m.blocks) {
                        val data = b.data ?: continue
                        parseSseEvent(SseEventType.from(b.type), data.toString())?.let { out += it }
                    }
                }
            }
        }
        return out
    }

    /** 商品详情（含 SKU 列表、营销文案、FAQ）— 供底部详情面板使用 */
    suspend fun getProduct(productId: String): Product = apiService.getProduct(productId)

    /** 直接加购（不走 SSE）— 供底部详情面板使用，加完返回最新购物车 */
    suspend fun addToCartDirect(productId: String, skuId: String, quantity: Int = 1): com.ragent.shopping.data.model.CartResponse {
        apiService.addToCart(
            com.ragent.shopping.data.model.AddCartRequest(
                sessionId = sessionId,
                productId = productId,
                skuId = skuId,
                quantity = quantity,
            )
        )
        return apiService.getCart(sessionId)
    }

    /**
     * 发起对话，返回 Flow<ChatMessage>。
     * text_delta 事件由 ViewModel 负责累积成流式文字气泡，Repository 只负责解析单条事件。
     */
    fun chat(text: String, imageBase64: String? = null): Flow<ChatMessage> {
        val body = gson.toJson(ChatRequest(sessionId, text, imageBase64))
        val url = "${NetworkConfig.BASE_URL}/api/v1/chat/stream"
        return sseClient.stream(url, body).mapNotNull { (type, data) ->
            parseSseEvent(SseEventType.from(type), data)
        }
    }

    /** 拍照找货，SSE 事件格式与 chat 完全一致，复用同一解析逻辑 */
    fun searchByImage(imageBase64: String): Flow<ChatMessage> {
        val body = gson.toJson(ImageSearchRequest(sessionId, imageBase64))
        val url = "${NetworkConfig.BASE_URL}/api/v1/search/by-image"
        return sseClient.stream(url, body).mapNotNull { (type, data) ->
            parseSseEvent(SseEventType.from(type), data)
        }
    }

    // ===== SSE 事件解析 =====

    private fun parseSseEvent(type: SseEventType, data: String): ChatMessage? {
        return try {
            val json = JsonParser.parseString(data).asJsonObject
            when (type) {
                SseEventType.THINKING ->
                    ChatMessage.AiStatus(json.get("message")?.asString ?: "正在思考...")

                SseEventType.TOOL_PROGRESS ->
                    ChatMessage.AiStatus(json.get("message")?.asString ?: "检索中...")

                SseEventType.IMAGE_SEARCHING ->
                    ChatMessage.AiStatus(json.get("message")?.asString ?: "正在分析图片...")

                SseEventType.TEXT_DELTA ->
                    ChatMessage.AiText(text = json.get("text")?.asString ?: "")

                SseEventType.PRODUCT_CARD ->
                    ChatMessage.AiProductCard(product = gson.fromJson(json, Product::class.java))

                SseEventType.PRODUCT_CARD_LIST -> {
                    val products = json.getAsJsonArray("products")
                        .map { gson.fromJson(it, Product::class.java) }
                    val searchType = json.get("search_type")?.asString ?: "text"
                    ChatMessage.AiProductList(products, searchType)
                }

                SseEventType.COMPARISON_TABLE ->
                    ChatMessage.AiComparison(
                        table = gson.fromJson(json, ComparisonTable::class.java)
                    )

                SseEventType.CLARIFICATION ->
                    ChatMessage.AiClarification(
                        question = json.get("question")?.asString ?: "",
                        options = json.getAsJsonArray("options")?.map { it.asString } ?: emptyList(),
                    )

                SseEventType.CART_UPDATE ->
                    ChatMessage.InternalCartUpdate(
                        action = json.get("action")?.asString ?: "",
                        totalCount = json.get("cart_total_count")?.asInt ?: 0,
                        totalPrice = json.get("cart_total_price")?.asDouble ?: 0.0,
                        toast = json.get("message")?.asString ?: "",
                    )

                SseEventType.DONE ->
                    ChatMessage.InternalDone(
                        agentState = json.get("agent_state")?.asString ?: AgentState.BROWSING.value
                    )

                SseEventType.ERROR ->
                    ChatMessage.AiError(
                        code = json.get("code")?.asString ?: "ERROR",
                        message = json.get("message")?.asString ?: "未知错误",
                    )

                SseEventType.UNKNOWN -> null
            }
        } catch (e: Exception) {
            null
        }
    }
}
