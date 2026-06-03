package com.ragent.shopping

import android.graphics.Bitmap
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.lifecycle.viewmodel.compose.viewModel
import androidx.navigation.NavType
import androidx.navigation.compose.NavHost
import androidx.navigation.compose.composable
import androidx.navigation.compose.rememberNavController
import androidx.navigation.navArgument
import com.ragent.shopping.ui.screen.CartScreen
import com.ragent.shopping.ui.screen.ChatScreen
import com.ragent.shopping.ui.screen.InAppCameraScreen
import com.ragent.shopping.ui.screen.OrderHistoryScreen
import com.ragent.shopping.ui.screen.ProductDetailScreen
import com.ragent.shopping.ui.viewmodel.ChatViewModel

@Composable
fun AppNavigation() {
    val navController = rememberNavController()
    // ChatViewModel 在导航图顶层创建，确保 sessionId 在整个 App 生命周期内共享
    val chatViewModel: ChatViewModel = viewModel()
    val uiState by chatViewModel.uiState.collectAsStateWithLifecycle()

    // 应用内相机拍到的图片通过共享状态传回 ChatScreen
    var capturedBitmap by remember { mutableStateOf<Bitmap?>(null) }

    NavHost(navController = navController, startDestination = "chat") {
        composable("chat") {
            ChatScreen(
                viewModel = chatViewModel,
                onNavigateToProduct = { productId ->
                    navController.navigate("product/$productId")
                },
                onNavigateToCart = {
                    navController.navigate("cart")
                },
                onNavigateToOrders = {
                    navController.navigate("orders")
                },
                onNavigateToCamera = {
                    navController.navigate("camera")
                },
                pendingCameraBitmap = capturedBitmap,
                onCameraBitmapConsumed = { capturedBitmap = null },
            )
        }

        composable("camera") {
            InAppCameraScreen(
                onImageCaptured = { bitmap ->
                    capturedBitmap = bitmap
                    navController.popBackStack()
                },
                onBack = { navController.popBackStack() },
            )
        }

        composable(
            route = "product/{productId}",
            arguments = listOf(navArgument("productId") { type = NavType.StringType }),
        ) { backStack ->
            val productId = backStack.arguments?.getString("productId") ?: return@composable
            ProductDetailScreen(
                productId = productId,
                sessionId = uiState.sessionId,
                onBack = { navController.popBackStack() },
            )
        }

        composable("cart") {
            CartScreen(
                sessionId = uiState.sessionId,
                onBack = { navController.popBackStack() },
                onCartChanged = { count, price -> chatViewModel.setCartBadge(count, price) },
            )
        }

        composable("orders") {
            OrderHistoryScreen(
                sessionId = uiState.sessionId,
                onBack = { navController.popBackStack() },
            )
        }
    }
}
