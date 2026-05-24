package com.ragent.shopping.ui.screen

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material.icons.filled.AddShoppingCart
import androidx.compose.material3.Button
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.FilterChip
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Scaffold
import androidx.compose.material3.SnackbarHost
import androidx.compose.material3.SnackbarHostState
import androidx.compose.material3.Text
import androidx.compose.material3.TopAppBar
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.produceState
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import coil.compose.AsyncImage
import com.ragent.shopping.data.model.Product
import com.ragent.shopping.data.model.Sku
import com.ragent.shopping.data.remote.ApiService
import com.ragent.shopping.data.remote.NetworkConfig
import com.ragent.shopping.data.repository.CartRepository
import kotlinx.coroutines.launch

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun ProductDetailScreen(
    productId: String,
    sessionId: String,
    onBack: () -> Unit,
) {
    val apiService = remember { ApiService() }
    val cartRepo = remember { CartRepository() }
    val snackbar = remember { SnackbarHostState() }
    val scope = rememberCoroutineScope()

    val productState by produceState<Product?>(null, productId) {
        value = try { apiService.getProduct(productId) } catch (_: Exception) { null }
    }

    var selectedSku by remember { mutableStateOf<Sku?>(null) }
    LaunchedEffect(productState) {
        selectedSku = productState?.skus?.firstOrNull()
    }

    Scaffold(
        topBar = {
            TopAppBar(
                title = { Text(productState?.title ?: "商品详情", maxLines = 1) },
                navigationIcon = {
                    IconButton(onClick = onBack) {
                        Icon(Icons.AutoMirrored.Filled.ArrowBack, contentDescription = "返回")
                    }
                },
            )
        },
        snackbarHost = { SnackbarHost(snackbar) },
        bottomBar = {
            productState?.let { product ->
                Button(
                    onClick = {
                        scope.launch {
                            try {
                                val skuId = selectedSku?.skuId ?: product.skus.firstOrNull()?.skuId ?: return@launch
                                cartRepo.addItem(sessionId, product.productId, skuId)
                                snackbar.showSnackbar("已加入购物车 🛒")
                            } catch (e: Exception) {
                                snackbar.showSnackbar("加购失败: ${e.message}")
                            }
                        }
                    },
                    modifier = Modifier
                        .fillMaxWidth()
                        .padding(16.dp),
                ) {
                    Icon(Icons.Default.AddShoppingCart, contentDescription = null)
                    Text("  加入购物车", style = MaterialTheme.typography.titleMedium)
                }
            }
        },
    ) { paddingValues ->
        val product = productState

        if (product == null) {
            Column(
                modifier = Modifier.fillMaxSize().padding(paddingValues),
                verticalArrangement = Arrangement.Center,
                horizontalAlignment = androidx.compose.ui.Alignment.CenterHorizontally,
            ) {
                CircularProgressIndicator()
                Spacer(Modifier.height(8.dp))
                Text("加载中...")
            }
            return@Scaffold
        }

        Column(
            modifier = Modifier
                .fillMaxSize()
                .padding(paddingValues)
                .verticalScroll(rememberScrollState()),
        ) {
            // 商品主图
            AsyncImage(
                model = NetworkConfig.imageUrl(product.imageUrl),
                contentDescription = product.title,
                modifier = Modifier
                    .fillMaxWidth()
                    .height(300.dp),
                contentScale = ContentScale.Crop,
            )

            Column(modifier = Modifier.padding(16.dp)) {
                // 标题和品牌
                Text(product.title, style = MaterialTheme.typography.titleLarge, fontWeight = FontWeight.Bold)
                Spacer(Modifier.height(4.dp))
                if (product.brand.isNotBlank()) {
                    Text(product.brand, style = MaterialTheme.typography.bodyMedium, color = MaterialTheme.colorScheme.outline)
                }
                Spacer(Modifier.height(8.dp))

                // 价格
                val displayPrice = selectedSku?.price?.takeIf { it > 0 } ?: product.displayPrice
                Text(
                    "¥%.2f".format(displayPrice),
                    style = MaterialTheme.typography.headlineSmall,
                    color = MaterialTheme.colorScheme.error,
                    fontWeight = FontWeight.Bold,
                )

                // SKU 选择
                if (product.skus.size > 1) {
                    Spacer(Modifier.height(12.dp))
                    Text("规格选择", style = MaterialTheme.typography.labelLarge, fontWeight = FontWeight.Medium)
                    Spacer(Modifier.height(6.dp))
                    Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                        product.skus.forEach { sku ->
                            val label = sku.properties.values.joinToString(" · ").ifBlank { sku.skuId }
                            FilterChip(
                                selected = selectedSku?.skuId == sku.skuId,
                                onClick = { selectedSku = sku },
                                label = { Text(label, style = MaterialTheme.typography.bodySmall) },
                                enabled = sku.stock > 0,
                            )
                        }
                    }
                }

                // 商品描述
                if (!product.marketingDescription.isNullOrBlank()) {
                    Spacer(Modifier.height(16.dp))
                    HorizontalDivider()
                    Spacer(Modifier.height(12.dp))
                    Text("商品详情", style = MaterialTheme.typography.titleSmall, fontWeight = FontWeight.Bold)
                    Spacer(Modifier.height(6.dp))
                    Text(product.marketingDescription, style = MaterialTheme.typography.bodyMedium, lineHeight = MaterialTheme.typography.bodyMedium.lineHeight)
                }

                // FAQ
                if (product.faq.isNotEmpty()) {
                    Spacer(Modifier.height(16.dp))
                    HorizontalDivider()
                    Spacer(Modifier.height(12.dp))
                    Text("常见问题", style = MaterialTheme.typography.titleSmall, fontWeight = FontWeight.Bold)
                    Spacer(Modifier.height(6.dp))
                    product.faq.forEach { faq ->
                        Text("Q: ${faq.question}", style = MaterialTheme.typography.bodyMedium, fontWeight = FontWeight.Medium)
                        Spacer(Modifier.height(2.dp))
                        Text("A: ${faq.answer}", style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.outline)
                        Spacer(Modifier.height(10.dp))
                    }
                }

                Spacer(Modifier.height(80.dp))
            }
        }
    }
}
