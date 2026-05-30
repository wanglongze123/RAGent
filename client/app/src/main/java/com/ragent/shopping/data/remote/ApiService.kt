package com.ragent.shopping.data.remote

import com.google.gson.Gson
import com.ragent.shopping.data.model.AddCartRequest
import com.ragent.shopping.data.model.CartItem
import com.ragent.shopping.data.model.CartResponse
import com.ragent.shopping.data.model.MessagesResponse
import com.ragent.shopping.data.model.Product
import com.ragent.shopping.data.model.SessionListResponse
import com.ragent.shopping.data.model.SessionResponse
import com.ragent.shopping.data.model.UpdateCartRequest
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import java.util.concurrent.TimeUnit

/**
 * REST API 封装（非流式接口），统一使用 OkHttp 同步调用 + Dispatchers.IO。
 */
class ApiService(private val gson: Gson = Gson()) {

    private val client = OkHttpClient.Builder()
        .connectTimeout(15, TimeUnit.SECONDS)
        .readTimeout(30, TimeUnit.SECONDS)
        .build()

    private val jsonType = "application/json; charset=utf-8".toMediaType()
    private val base = NetworkConfig.BASE_URL

    // ===== 会话 =====

    suspend fun createSession(): SessionResponse = withContext(Dispatchers.IO) {
        val request = Request.Builder()
            .url("$base/api/v1/sessions")
            .post("{}".toRequestBody(jsonType))
            .build()
        client.newCall(request).execute().use { response ->
            if (!response.isSuccessful) throw Exception("创建会话失败: ${response.code}")
            gson.fromJson(response.body!!.string(), SessionResponse::class.java)
        }
    }

    suspend fun getSessions(): SessionListResponse = withContext(Dispatchers.IO) {
        val request = Request.Builder()
            .url("$base/api/v1/sessions")
            .get()
            .build()
        client.newCall(request).execute().use { response ->
            if (!response.isSuccessful) throw Exception("获取会话列表失败: ${response.code}")
            gson.fromJson(response.body!!.string(), SessionListResponse::class.java)
        }
    }

    suspend fun getMessages(sessionId: String): MessagesResponse = withContext(Dispatchers.IO) {
        val request = Request.Builder()
            .url("$base/api/v1/sessions/$sessionId/messages")
            .get()
            .build()
        client.newCall(request).execute().use { response ->
            if (!response.isSuccessful) throw Exception("获取历史失败: ${response.code}")
            gson.fromJson(response.body!!.string(), MessagesResponse::class.java)
        }
    }

    // ===== 商品详情 =====

    suspend fun getProduct(productId: String): Product = withContext(Dispatchers.IO) {
        val request = Request.Builder()
            .url("$base/api/v1/products/$productId")
            .get()
            .build()
        client.newCall(request).execute().use { response ->
            if (!response.isSuccessful) throw Exception("商品不存在: $productId")
            gson.fromJson(response.body!!.string(), Product::class.java)
        }
    }

    // ===== 购物车 =====

    suspend fun getCart(sessionId: String): CartResponse = withContext(Dispatchers.IO) {
        val request = Request.Builder()
            .url("$base/api/v1/cart?session_id=$sessionId")
            .get()
            .build()
        client.newCall(request).execute().use { response ->
            if (!response.isSuccessful) throw Exception("获取购物车失败: ${response.code}")
            gson.fromJson(response.body!!.string(), CartResponse::class.java)
        }
    }

    suspend fun addToCart(req: AddCartRequest): CartItem = withContext(Dispatchers.IO) {
        val body = gson.toJson(req).toRequestBody(jsonType)
        val request = Request.Builder()
            .url("$base/api/v1/cart")
            .post(body)
            .build()
        client.newCall(request).execute().use { response ->
            if (!response.isSuccessful) throw Exception("加购失败: ${response.code}")
            gson.fromJson(response.body!!.string(), CartItem::class.java)
        }
    }

    suspend fun updateCartItem(cartItemId: String, req: UpdateCartRequest): Unit =
        withContext(Dispatchers.IO) {
            val body = gson.toJson(req).toRequestBody(jsonType)
            val request = Request.Builder()
                .url("$base/api/v1/cart/$cartItemId")
                .put(body)
                .build()
            client.newCall(request).execute().use { response ->
                if (!response.isSuccessful) throw Exception("更新失败: ${response.code}")
            }
        }

    suspend fun deleteCartItem(cartItemId: String, sessionId: String): Unit =
        withContext(Dispatchers.IO) {
            val request = Request.Builder()
                .url("$base/api/v1/cart/$cartItemId?session_id=$sessionId")
                .delete()
                .build()
            client.newCall(request).execute().use { response ->
                if (!response.isSuccessful) throw Exception("删除失败: ${response.code}")
            }
        }

    suspend fun clearCart(sessionId: String): Unit = withContext(Dispatchers.IO) {
        val request = Request.Builder()
            .url("$base/api/v1/cart?session_id=$sessionId")
            .delete()
            .build()
        client.newCall(request).execute().use { response ->
            if (!response.isSuccessful) throw Exception("清空失败: ${response.code}")
        }
    }
}
