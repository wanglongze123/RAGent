package com.ragent.shopping.ui.viewmodel

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.ragent.shopping.data.model.CartItem
import com.ragent.shopping.data.repository.CartRepository
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch

data class CartUiState(
    val items: List<CartItem> = emptyList(),
    val totalPrice: Double = 0.0,
    val totalCount: Int = 0,
    val isLoading: Boolean = false,
    val error: String = "",
)

class CartViewModel(
    private val cartRepo: CartRepository = CartRepository(),
) : ViewModel() {

    private val _uiState = MutableStateFlow(CartUiState())
    val uiState: StateFlow<CartUiState> = _uiState.asStateFlow()

    fun loadCart(sessionId: String) {
        if (sessionId.isBlank()) return
        viewModelScope.launch {
            _uiState.update { it.copy(isLoading = true, error = "") }
            try {
                val cart = cartRepo.getCart(sessionId)
                _uiState.update {
                    it.copy(
                        items = cart.items,
                        totalPrice = cart.totalPrice,
                        totalCount = cart.totalCount,
                        isLoading = false,
                    )
                }
            } catch (e: Exception) {
                _uiState.update { it.copy(isLoading = false, error = e.message ?: "加载失败") }
            }
        }
    }

    fun increaseQuantity(item: CartItem, sessionId: String) {
        viewModelScope.launch {
            try {
                cartRepo.updateQuantity(item.cartItemId, sessionId, item.quantity + 1)
                loadCart(sessionId)
            } catch (_: Exception) {}
        }
    }

    fun decreaseQuantity(item: CartItem, sessionId: String) {
        if (item.quantity <= 1) {
            deleteItem(item, sessionId)
            return
        }
        viewModelScope.launch {
            try {
                cartRepo.updateQuantity(item.cartItemId, sessionId, item.quantity - 1)
                loadCart(sessionId)
            } catch (_: Exception) {}
        }
    }

    fun deleteItem(item: CartItem, sessionId: String) {
        viewModelScope.launch {
            try {
                cartRepo.deleteItem(item.cartItemId, sessionId)
                loadCart(sessionId)
            } catch (_: Exception) {}
        }
    }
}
