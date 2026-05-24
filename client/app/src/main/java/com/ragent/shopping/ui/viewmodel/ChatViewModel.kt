package com.ragent.shopping.ui.viewmodel

import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.util.Base64
import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.ragent.shopping.data.model.AgentState
import com.ragent.shopping.data.model.ChatMessage
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
)

class ChatViewModel(
    private val chatRepo: ChatRepository = ChatRepository(),
) : ViewModel() {

    private val _uiState = MutableStateFlow(ChatUiState())
    val uiState: StateFlow<ChatUiState> = _uiState.asStateFlow()

    init {
        viewModelScope.launch {
            val sid = chatRepo.ensureSession()
            _uiState.update { it.copy(sessionId = sid) }
        }
    }

    // ===== 发送文字消息 =====

    fun sendMessage(text: String) {
        if (text.isBlank() || _uiState.value.isLoading) return
        viewModelScope.launch {
            chatRepo.ensureSession()
            appendUserMessage(text)
            streamFromRepo { chatRepo.chat(text) }
        }
    }

    // ===== 拍照找货 =====

    fun searchByImagePath(imagePath: String) {
        viewModelScope.launch {
            chatRepo.ensureSession()
            appendUserMessage("[拍照找货]")
            val base64 = compressToBase64(imagePath) ?: return@launch
            streamFromRepo { chatRepo.searchByImage(base64) }
        }
    }

    fun searchByImageBase64(base64: String) {
        if (base64.isBlank() || _uiState.value.isLoading) return
        viewModelScope.launch {
            chatRepo.ensureSession()
            appendUserMessage("[拍照找货]")
            streamFromRepo { chatRepo.searchByImage(base64) }
        }
    }

    // ===== 点击反问选项：等同于发送该文字 =====

    fun selectClarification(option: String) = sendMessage(option)

    // ===== 新建会话 =====

    fun newSession() {
        chatRepo.resetSession()
        _uiState.update {
            ChatUiState(cartBadgeCount = it.cartBadgeCount, cartTotalPrice = it.cartTotalPrice)
        }
        viewModelScope.launch {
            val sid = chatRepo.ensureSession()
            _uiState.update { it.copy(sessionId = sid) }
        }
    }

    fun clearToast() = _uiState.update { it.copy(toastMessage = "") }

    // ===== 内部辅助 =====

    private fun appendUserMessage(text: String) {
        _uiState.update { state ->
            state.copy(
                messages = state.messages + ChatMessage.User(text),
                isLoading = true,
            )
        }
        addStatus("正在思考...")
    }

    private fun addStatus(msg: String) {
        _uiState.update { state ->
            // 替换已有 status，避免堆积
            val idx = state.messages.indexOfLast { it is ChatMessage.AiStatus }
            val newMessages = if (idx >= 0) {
                state.messages.toMutableList().apply { set(idx, ChatMessage.AiStatus(msg)) }
            } else {
                state.messages + ChatMessage.AiStatus(msg)
            }
            state.copy(messages = newMessages)
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
                    // 追加到最后一个流式气泡
                    val idx = state.messages.indexOfLast {
                        it is ChatMessage.AiText && (it as ChatMessage.AiText).isStreaming
                    }
                    if (idx >= 0) {
                        val existing = state.messages[idx] as ChatMessage.AiText
                        val updated = state.messages.toMutableList().apply {
                            set(idx, existing.copy(text = existing.text + event.text))
                        }
                        state.copy(messages = updated)
                    } else {
                        // 移除 status，开始新的流式气泡
                        val withoutStatus = state.messages.filter { it !is ChatMessage.AiStatus }
                        state.copy(messages = withoutStatus + ChatMessage.AiText(event.text, isStreaming = true))
                    }
                }

                is ChatMessage.AiProductCard,
                is ChatMessage.AiProductList,
                is ChatMessage.AiComparison,
                is ChatMessage.AiClarification,
                is ChatMessage.AiError -> {
                    val finalized = finalizeStreamingText(state.messages)
                        .filter { it !is ChatMessage.AiStatus }
                    state.copy(messages = finalized + event)
                }

                is ChatMessage.InternalCartUpdate ->
                    state.copy(
                        cartBadgeCount = event.totalCount,
                        cartTotalPrice = event.totalPrice,
                        toastMessage = event.toast,
                    )

                is ChatMessage.InternalDone ->
                    state.copy(agentState = AgentState.from(event.agentState))

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
