package com.ragent.shopping.data.model

import com.google.gson.annotations.SerializedName

// ===== SSE 事件类型（对应接口文档第6节）=====

enum class SseEventType(val value: String) {
    THINKING("thinking"),
    TOOL_PROGRESS("tool_progress"),
    TEXT_DELTA("text_delta"),
    PRODUCT_CARD("product_card"),
    PRODUCT_CARD_LIST("product_card_list"),
    COMPARISON_TABLE("comparison_table"),
    CART_UPDATE("cart_update"),
    CLARIFICATION("clarification"),
    IMAGE_SEARCHING("image_searching"),
    ERROR("error"),
    DONE("done"),
    UNKNOWN("unknown");

    companion object {
        fun from(value: String): SseEventType = entries.find { it.value == value } ?: UNKNOWN
    }
}

// ===== 商品相关模型 =====

data class Product(
    @SerializedName("product_id") val productId: String = "",
    @SerializedName("title") val title: String = "",
    @SerializedName("brand") val brand: String = "",
    @SerializedName("category") val category: String = "",
    @SerializedName("sub_category") val subCategory: String = "",
    @SerializedName("base_price") val basePrice: Double = 0.0,
    @SerializedName("image_url") val imageUrl: String = "",
    @SerializedName("price") val price: Double = 0.0,
    @SerializedName("reason") val reason: String? = null,
    @SerializedName("similarity_score") val similarityScore: Double? = null,
    @SerializedName("skus") val skus: List<Sku> = emptyList(),
    @SerializedName("marketing_description") val marketingDescription: String? = null,
    @SerializedName("faq") val faq: List<Faq> = emptyList(),
) {
    // price 字段用于对话中返回的简化商品，base_price 用于完整商品详情
    val displayPrice: Double get() = if (price > 0) price else basePrice
}

data class Sku(
    @SerializedName("sku_id") val skuId: String,
    @SerializedName("properties") val properties: Map<String, String>,
    @SerializedName("price") val price: Double,
    @SerializedName("stock") val stock: Int,
)

data class Faq(
    @SerializedName("question") val question: String,
    @SerializedName("answer") val answer: String,
)

// ===== 商品对比表格 =====

data class ComparisonTable(
    @SerializedName("products") val products: List<Product>,
    @SerializedName("dimensions") val dimensions: List<ComparisonDimension>,
    @SerializedName("recommendation") val recommendation: ComparisonRecommendation?,
)

data class ComparisonDimension(
    @SerializedName("name") val name: String,
    @SerializedName("values") val values: List<String>,
)

data class ComparisonRecommendation(
    @SerializedName("product_id") val productId: String,
    @SerializedName("reason") val reason: String,
)

// ===== 购物车 =====

data class CartItem(
    @SerializedName("cart_item_id") val cartItemId: String,
    @SerializedName("product_id") val productId: String,
    @SerializedName("sku_id") val skuId: String,
    @SerializedName("title") val title: String,
    @SerializedName("image_url") val imageUrl: String,
    @SerializedName("sku_props") val skuProperties: Map<String, String>?,
    @SerializedName("unit_price") val unitPrice: Double,
    @SerializedName("quantity") val quantity: Int,
    @SerializedName("subtotal") val subtotal: Double,
)

data class CartResponse(
    @SerializedName("session_id") val sessionId: String,
    @SerializedName("items") val items: List<CartItem>,
    @SerializedName("total_count") val totalCount: Int,
    @SerializedName("total_price") val totalPrice: Double,
)

// ===== 会话 =====

data class SessionResponse(
    @SerializedName("session_id") val sessionId: String,
    @SerializedName("created_at") val createdAt: String,
)

// ===== API 请求体 =====

data class ChatRequest(
    @SerializedName("session_id") val sessionId: String,
    @SerializedName("message") val message: String,
    @SerializedName("image_base64") val imageBase64: String? = null,
)

data class ImageSearchRequest(
    @SerializedName("session_id") val sessionId: String,
    @SerializedName("image_base64") val imageBase64: String,
    @SerializedName("image_mime_type") val imageMimeType: String = "image/jpeg",
)

data class AddCartRequest(
    @SerializedName("session_id") val sessionId: String,
    @SerializedName("product_id") val productId: String,
    @SerializedName("sku_id") val skuId: String,
    @SerializedName("quantity") val quantity: Int = 1,
)

data class UpdateCartRequest(
    @SerializedName("session_id") val sessionId: String,
    @SerializedName("quantity") val quantity: Int,
)

// ===== UI 消息模型（LazyColumn 中展示的每一条） =====

sealed class ChatMessage {
    // 用户发送的文字
    data class User(val text: String) : ChatMessage()

    // AI 流式文字，isStreaming=true 时末尾显示光标
    data class AiText(val text: String, val isStreaming: Boolean = false) : ChatMessage()

    // 加载/思考状态
    data class AiStatus(val message: String) : ChatMessage()

    // 单个商品卡片
    data class AiProductCard(val product: Product) : ChatMessage()

    // 商品列表（横滑）
    data class AiProductList(val products: List<Product>, val searchType: String = "text") : ChatMessage()

    // 多商品对比表格
    data class AiComparison(val table: ComparisonTable) : ChatMessage()

    // Agent 主动反问 + 选项按钮
    data class AiClarification(val question: String, val options: List<String>) : ChatMessage()

    // 错误提示
    data class AiError(val code: String, val message: String) : ChatMessage()

    // 购物车更新（不在消息流中显示，由 ViewModel 处理）
    data class InternalCartUpdate(
        val action: String,
        val totalCount: Int,
        val totalPrice: Double,
        val toast: String,
    ) : ChatMessage()

    // 本轮回复结束（不显示，更新 agentState）
    data class InternalDone(val agentState: String) : ChatMessage()
}

// ===== Agent 状态 =====

enum class AgentState(val value: String) {
    BROWSING("browsing"),
    COMPARING("comparing"),
    CART_MANAGEMENT("cart_management"),
    CHECKOUT("checkout");

    companion object {
        fun from(value: String): AgentState = entries.find { it.value == value } ?: BROWSING
    }
}
