package com.ragent.shopping.data.remote

import com.ragent.shopping.data.local.DeviceId
import kotlinx.coroutines.channels.awaitClose
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.callbackFlow
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import okhttp3.Response
import okhttp3.sse.EventSource
import okhttp3.sse.EventSourceListener
import okhttp3.sse.EventSources
import java.io.IOException
import java.util.concurrent.TimeUnit

/**
 * 基于 OkHttp EventSource 的 SSE 客户端，将回调桥接为 Kotlin Flow。
 * 每个元素是 Pair(eventType, dataJson)，由上层 Repository 负责解析业务含义。
 */
class SseClient {

    // SSE 连接需要长超时，读取超时设为 0（无限）
    private val okHttpClient = OkHttpClient.Builder()
        .connectTimeout(15, TimeUnit.SECONDS)
        .readTimeout(0, TimeUnit.MILLISECONDS)
        .writeTimeout(30, TimeUnit.SECONDS)
        // 每个请求带上设备标识，服务端据此隔离会话
        .addInterceptor { chain ->
            chain.proceed(
                chain.request().newBuilder()
                    .header("X-Device-Id", DeviceId.get())
                    .build()
            )
        }
        .build()

    fun stream(url: String, jsonBody: String): Flow<Pair<String, String>> = callbackFlow {
        val body = jsonBody.toRequestBody("application/json; charset=utf-8".toMediaType())
        val request = Request.Builder()
            .url(url)
            .post(body)
            .header("Accept", "text/event-stream")
            .header("Cache-Control", "no-cache")
            .build()

        val eventSource = EventSources.createFactory(okHttpClient)
            .newEventSource(request, object : EventSourceListener() {
                override fun onEvent(
                    eventSource: EventSource,
                    id: String?,
                    type: String?,
                    data: String,
                ) {
                    val t = type ?: return
                    trySend(Pair(t, data))
                }

                override fun onFailure(
                    eventSource: EventSource,
                    t: Throwable?,
                    response: Response?,
                ) {
                    val error = t ?: IOException("SSE 连接失败: HTTP ${response?.code}")
                    close(error)
                }

                override fun onClosed(eventSource: EventSource) {
                    close()
                }
            })

        awaitClose { eventSource.cancel() }
    }
}
