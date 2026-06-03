package com.ragent.shopping.ui.viewmodel

import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.util.Base64
import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.ragent.shopping.data.model.AgentState
import com.ragent.shopping.data.model.ChatMessage
import com.ragent.shopping.data.model.SessionSummary
import com.ragent.shopping.data.local.SessionPrefs
import com.ragent.shopping.data.repository.ChatRepository
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.catch
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import java.io.ByteArrayOutputStream

data class ChatUiState(
    val messages: List<ChatMessage> = emptyList(),
    val isLoading: Boolean = false,
    val cartBadgeCount: Int = 0,
    val cartTotalPrice: Double = 0.0,
    val agentState: AgentState = AgentState.BROWSING,
    val sessionId: String = "",
    val toastMessage: String = "",
    // 商品详情面板：null 表示不展示，非 null 时展示底部弹层
    val detailProduct: com.ragent.shopping.data.model.Product? = null,
    val isLoadingDetail: Boolean = false,
    // 会话列表（抽屉）与历史恢复中标志
    val sessions: List<SessionSummary> = emptyList(),
    val isRestoring: Boolean = false,
    // 自动滚到底的信号：每次有新内容出现就 +1，ChatScreen 监听此值
    val scrollTick: Int = 0,
    // 收货信息表单 BottomSheet
    val showOrderForm: Boolean = false,
    val orderFormAddresses: List<com.ragent.shopping.data.model.SavedAddress> = emptyList(),
    // 上次填写的收货信息（跨会话预填）
    val lastUsedName: String = "",
    val lastUsedPhone: String = "",
    val lastUsedAddress: String = "",
)

class ChatViewModel(
    private val chatRepo: ChatRepository = ChatRepository(),
) : ViewModel() {

    private val _uiState = MutableStateFlow(ChatUiState())
    val uiState: StateFlow<ChatUiState> = _uiState.asStateFlow()

    init {
        viewModelScope.launch {
            _uiState.update { it.copy(isRestoring = true) }
            val sid = chatRepo.ensureSession()
            // 恢复该会话历史消息 + 购物车角标（重启续上下文）。失败兜底为空，不阻塞首屏。
            val history = runCatching { chatRepo.loadHistory(sid) }.getOrDefault(emptyList())
            val cart = runCatching { chatRepo.getCart() }.getOrNull()
            _uiState.update {
                it.copy(
                    sessionId = sid,
                    messages = history,
                    isRestoring = false,
                    cartBadgeCount = cart?.totalCount ?: 0,
                    cartTotalPrice = cart?.totalPrice ?: 0.0,
                )
            }
            refreshSessions()
        }
    }

    /** 刷新会话列表（打开抽屉/每轮对话后调用） */
    fun refreshSessions() {
        viewModelScope.launch {
            val list = runCatching { chatRepo.loadSessions() }.getOrDefault(emptyList())
            _uiState.update { it.copy(sessions = list) }
        }
    }

    /** 切换到历史会话：回填历史 + 同步该会话购物车角标 */
    fun switchSession(id: String) {
        if (id == _uiState.value.sessionId || _uiState.value.isRestoring) return
        viewModelScope.launch {
            _uiState.update { it.copy(isRestoring = true) }
            chatRepo.switchSession(id)
            val history = runCatching { chatRepo.loadHistory(id) }.getOrDefault(emptyList())
            val cart = runCatching { chatRepo.getCart() }.getOrNull()
            _uiState.update {
                it.copy(
                    sessionId = id,
                    messages = history,
                    isRestoring = false,
                    cartBadgeCount = cart?.totalCount ?: 0,
                    cartTotalPrice = cart?.totalPrice ?: 0.0,
                    agentState = AgentState.BROWSING,
                )
            }
            refreshSessions()
        }
    }

    // ===== 发送消息（文字 / 图片 / 图文混合）=====

    fun sendMessage(text: String, imageBase64: String? = null, bitmap: Bitmap? = null) {
        if (text.isBlank() && imageBase64 == null) return
        if (_uiState.value.isLoading) return
        viewModelScope.launch {
            chatRepo.ensureSession()
            _uiState.update { state ->
                state.copy(
                    messages = state.messages + ChatMessage.User(text, bitmap),
                    isLoading = true,
                )
            }
            addStatus("正在思考...")
            streamFromRepo { chatRepo.chat(text, imageBase64) }
        }
    }

    // ===== 点击反问选项（不在对话流中显示用户气泡）=====

    fun selectClarification(option: String) {
        // 移除最后一条 clarification，避免选完后留着占空白
        _uiState.update { state ->
            val idx = state.messages.indexOfLast { it is ChatMessage.AiClarification }
            if (idx >= 0) state.copy(messages = state.messages.toMutableList().apply { removeAt(idx) })
            else state
        }
        // 直接发送到后端，不追加用户气泡——点按钮不等于打字
        if (_uiState.value.isLoading) return
        viewModelScope.launch {
            chatRepo.ensureSession()
            _uiState.update { it.copy(isLoading = true) }
            addStatus("正在思考...")
            streamFromRepo { chatRepo.chat(option) }
        }
    }

    // 旧接口兼容（供外部直接传 base64，不带 bitmap 预览）
    fun searchByImageBase64(base64: String) = sendMessage("", base64, null)

    // ===== 商品详情底部面板 =====

    fun openProductDetail(productId: String) {
        if (_uiState.value.isLoadingDetail) return
        _uiState.update { it.copy(isLoadingDetail = true) }
        viewModelScope.launch {
            try {
                val product = chatRepo.getProduct(productId)
                _uiState.update { it.copy(detailProduct = product, isLoadingDetail = false) }
            } catch (e: Exception) {
                _uiState.update {
                    it.copy(
                        isLoadingDetail = false,
                        toastMessage = "加载商品详情失败：${e.message ?: ""}",
                    )
                }
            }
        }
    }

    fun closeProductDetail() {
        _uiState.update { it.copy(detailProduct = null) }
    }

    /**
     * Module C: 详情面板里点击"加入购物车"——直接调 REST API，不走 AI/SSE 流。
     * 流程：
     *   1) 关闭底部面板（即时反馈）
     *   2) 调 cart API
     *   3) 成功 → 更新购物车角标 + toast + 注入引导消息（"接下来？"）
     *   4) 失败 → 错误 toast
     */
    fun addToCartDirect(productId: String, skuId: String, productTitle: String) {
        // 关闭面板，给即时反馈
        _uiState.update { it.copy(detailProduct = null) }
        viewModelScope.launch {
            try {
                val cart = chatRepo.addToCartDirect(productId, skuId)
                _uiState.update { state ->
                    val confirmation = ChatMessage.AiClarification(
                        question = "✓ 已将「$productTitle」加入购物车，接下来？",
                        options = listOf("帮我下单", "查看购物车"),
                    )
                    state.copy(
                        messages = state.messages + confirmation,
                        cartBadgeCount = cart.totalCount,
                        cartTotalPrice = cart.totalPrice,
                        toastMessage = "已加入购物车",
                    )
                }
            } catch (e: Exception) {
                _uiState.update {
                    it.copy(toastMessage = "加购失败：${e.message ?: "网络错误"}")
                }
            }
        }
    }

    // ===== 新建会话 =====

    fun newSession() {
        if (_uiState.value.isRestoring) return
        viewModelScope.launch {
            val sid = chatRepo.newSession()
            _uiState.update {
                it.copy(
                    sessionId = sid,
                    messages = emptyList(),
                    cartBadgeCount = 0,
                    cartTotalPrice = 0.0,
                    agentState = AgentState.BROWSING,
                    toastMessage = "",
                )
            }
            refreshSessions()
        }
    }

    fun clearToast() = _uiState.update { it.copy(toastMessage = "") }

    /** 用户提交收货信息表单（不显示用户气泡，同时持久化到 DataStore 跨会话复用） */
    fun submitOrderForm(name: String, phone: String, address: String) {
        _uiState.update { it.copy(showOrderForm = false, orderFormAddresses = emptyList()) }
        if (_uiState.value.isLoading) return
        val n = name.trim(); val p = phone.trim(); val a = address.trim()
        val payload = """{"name":"$n","phone":"$p","address":"$a"}"""
        viewModelScope.launch {
            SessionPrefs.saveReceiverInfo(n, p, a)   // 全局持久化，换会话不丢
            chatRepo.ensureSession()
            _uiState.update { it.copy(isLoading = true) }
            addStatus("正在思考...")
            streamFromRepo { chatRepo.chat("ORDER_INFO:$payload") }
        }
    }

    /** 用户关闭/取消收货信息表单（不显示用户气泡） */
    fun dismissOrderForm() {
        _uiState.update { it.copy(showOrderForm = false, orderFormAddresses = emptyList()) }
        if (_uiState.value.isLoading) return
        viewModelScope.launch {
            chatRepo.ensureSession()
            _uiState.update { it.copy(isLoading = true) }
            addStatus("正在思考...")
            streamFromRepo { chatRepo.chat("取消") }
        }
    }

    /**
     * 同步购物车角标 —— 供购物车页（CartScreen）增删改后回传最新数量/金额。
     * 购物车页用的是独立的 CartViewModel，对话页顶栏角标读的是这里的 cartBadgeCount，
     * 不回传就会出现「购物车页删了商品、对话页角标不变」的状态不一致。
     */
    fun setCartBadge(count: Int, price: Double) {
        _uiState.update { it.copy(cartBadgeCount = count, cartTotalPrice = price) }
    }

    // ===== 内部辅助 =====

    private fun addStatus(msg: String) {
        _uiState.update { state ->
            val idx = state.messages.indexOfLast { it is ChatMessage.AiStatus }
            val newMessages = if (idx >= 0) {
                state.messages.toMutableList().apply { set(idx, ChatMessage.AiStatus(msg)) }
            } else {
                state.messages + ChatMessage.AiStatus(msg)
            }
            state.copy(messages = newMessages, scrollTick = state.scrollTick + 1)
        }
    }

    private fun streamFromRepo(flowProducer: () -> kotlinx.coroutines.flow.Flow<ChatMessage>) {
        viewModelScope.launch {
            flowProducer()
                .catch { e ->
                    finalizeStream()
                    appendErrorMessage("STREAM_ERROR", e.message ?: "网络连接失败")
                }
                .collect { event -> handleEvent(event) }

            // 收集完毕，确保流式文字已锁定、状态已清除
            finalizeStream()
            // 刷新会话列表：首轮对话会让新会话出现在抽屉、并更新预览/排序
            refreshSessions()
        }
    }

    /**
     * 核心事件处理逻辑：
     * - text_delta：追加到当前流式气泡（或新建）
     * - 商品/对比/反问组件：先锁定当前流式文字，再追加组件
     * - internal 事件：更新 ViewModel 状态，不加入消息列表
     */
    private fun handleEvent(event: ChatMessage) {
        _uiState.update { state ->
            when (event) {
                is ChatMessage.AiStatus ->
                    state.copy(messages = replaceOrAddStatus(state.messages, event))

                is ChatMessage.AiText -> {
                    val idx = state.messages.indexOfLast {
                        it is ChatMessage.AiText && (it as ChatMessage.AiText).isStreaming
                    }
                    if (idx >= 0) {
                        // 追加到已有流式气泡，不触发滚动（lastStreamingText 负责）
                        val existing = state.messages[idx] as ChatMessage.AiText
                        val updated = state.messages.toMutableList().apply {
                            set(idx, existing.copy(text = existing.text + event.text))
                        }
                        state.copy(messages = updated)
                    } else {
                        // 新流式气泡出现，触发一次滚动
                        val withoutStatus = state.messages.filter { it !is ChatMessage.AiStatus }
                        state.copy(
                            messages = withoutStatus + ChatMessage.AiText(event.text, isStreaming = true),
                            scrollTick = state.scrollTick + 1,
                        )
                    }
                }

                is ChatMessage.AiProductCard,
                is ChatMessage.AiProductList,
                is ChatMessage.AiComparison,
                is ChatMessage.AiClarification,
                is ChatMessage.AiError -> {
                    val finalized = finalizeStreamingText(state.messages)
                        .filter { it !is ChatMessage.AiStatus }
                    // 新卡片/选择框出现，触发滚动
                    state.copy(messages = finalized + event, scrollTick = state.scrollTick + 1)
                }

                is ChatMessage.InternalCartUpdate ->
                    state.copy(
                        cartBadgeCount = event.totalCount,
                        cartTotalPrice = event.totalPrice,
                        toastMessage = event.toast,
                    )

                is ChatMessage.InternalDone ->
                    state.copy(agentState = AgentState.from(event.agentState))

                is ChatMessage.InternalOrderForm -> {
                    // 先标记要弹表单，同时异步加载 DataStore 里的上次收货信息
                    viewModelScope.launch {
                        val (name, phone, address) = SessionPrefs.getLastReceiverInfo()
                        _uiState.update {
                            it.copy(
                                showOrderForm = true,
                                orderFormAddresses = event.savedAddresses,
                                lastUsedName = name,
                                lastUsedPhone = phone,
                                lastUsedAddress = address,
                            )
                        }
                    }
                    state  // 立即返回原 state，避免在 update 里嵌套 update
                }

                else -> state
            }
        }
    }

    private fun finalizeStream() {
        _uiState.update { state ->
            state.copy(
                messages = finalizeStreamingText(state.messages)
                    .filter { it !is ChatMessage.AiStatus },
                isLoading = false,
            )
        }
    }

    private fun appendErrorMessage(code: String, msg: String) {
        _uiState.update { state ->
            state.copy(messages = state.messages + ChatMessage.AiError(code, msg))
        }
    }

    private fun replaceOrAddStatus(messages: List<ChatMessage>, status: ChatMessage.AiStatus): List<ChatMessage> {
        val idx = messages.indexOfLast { it is ChatMessage.AiStatus }
        return if (idx >= 0) messages.toMutableList().apply { set(idx, status) }
        else messages + status
    }

    private fun finalizeStreamingText(messages: List<ChatMessage>): List<ChatMessage> =
        messages.map { msg ->
            if (msg is ChatMessage.AiText && msg.isStreaming) msg.copy(isStreaming = false)
            else msg
        }

    // 压缩图片并转 Base64（< 500KB）
    private fun compressToBase64(imagePath: String): String? {
        return try {
            val bitmap = BitmapFactory.decodeFile(imagePath) ?: return null
            val scaled = if (bitmap.width > 800 || bitmap.height > 800) {
                val ratio = minOf(800f / bitmap.width, 800f / bitmap.height)
                Bitmap.createScaledBitmap(bitmap, (bitmap.width * ratio).toInt(), (bitmap.height * ratio).toInt(), true)
            } else bitmap
            val output = ByteArrayOutputStream()
            scaled.compress(Bitmap.CompressFormat.JPEG, 80, output)
            Base64.encodeToString(output.toByteArray(), Base64.NO_WRAP)
        } catch (e: Exception) {
            null
        }
    }
}
