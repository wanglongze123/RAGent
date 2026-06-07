package com.ragent.shopping.data.repository

import com.ragent.shopping.data.model.AddCartRequest
import com.ragent.shopping.data.model.CartItem
import com.ragent.shopping.data.model.CartResponse
import com.ragent.shopping.data.model.UpdateCartRequest
import com.ragent.shopping.data.remote.ApiService

class CartRepository(private val apiService: ApiService = ApiService()) {

    suspend fun getCart(sessionId: String): CartResponse = apiService.getCart(sessionId)

    suspend fun addItem(sessionId: String, productId: String, skuId: String): CartItem =
        apiService.addToCart(AddCartRequest(sessionId, productId, skuId))

    suspend fun updateQuantity(cartItemId: String, sessionId: String, quantity: Int) =
        apiService.updateCartItem(cartItemId, UpdateCartRequest(sessionId, quantity))

    suspend fun deleteItem(cartItemId: String, sessionId: String) =
        apiService.deleteCartItem(cartItemId, sessionId)

    suspend fun clearCart(sessionId: String) = apiService.clearCart(sessionId)
}
