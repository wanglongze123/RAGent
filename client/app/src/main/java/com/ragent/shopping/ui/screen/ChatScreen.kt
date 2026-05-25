package com.ragent.shopping.ui.screen

import android.Manifest
import android.content.pm.PackageManager
import android.graphics.Bitmap
import android.net.Uri
import android.util.Base64
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.core.content.FileProvider
import java.io.File
import androidx.compose.animation.AnimatedVisibility
import androidx.compose.animation.core.RepeatMode
import androidx.compose.animation.core.animateFloat
import androidx.compose.animation.core.infiniteRepeatable
import androidx.compose.animation.core.rememberInfiniteTransition
import androidx.compose.animation.core.tween
import androidx.compose.animation.expandVertically
import androidx.compose.animation.fadeIn
import androidx.compose.animation.fadeOut
import androidx.compose.animation.shrinkVertically
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.WindowInsets
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.ime
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.layout.windowInsetsPadding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.LazyRow
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.pager.HorizontalPager
import androidx.compose.foundation.pager.rememberPagerState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.Send
import androidx.compose.material.icons.filled.AddShoppingCart
import androidx.compose.material.icons.filled.CameraAlt
import androidx.compose.material.icons.filled.Close
import androidx.compose.material.icons.filled.Image
import androidx.compose.material.icons.filled.ShoppingCart
import androidx.compose.material3.Badge
import androidx.compose.material3.BadgedBox
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.FilterChip
import androidx.compose.material3.FilterChipDefaults
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.ModalBottomSheet
import androidx.compose.material3.rememberModalBottomSheetState
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Scaffold
import androidx.compose.material3.SnackbarHost
import androidx.compose.material3.SnackbarHostState
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TopAppBar
import androidx.compose.material3.TopAppBarDefaults
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.core.content.ContextCompat
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.lifecycle.viewmodel.compose.viewModel
import coil.compose.AsyncImage
import com.ragent.shopping.data.model.ChatMessage
import com.ragent.shopping.data.model.ComparisonTable
import com.ragent.shopping.data.model.Product
import com.ragent.shopping.data.remote.NetworkConfig
import com.ragent.shopping.ui.viewmodel.ChatViewModel
import kotlinx.coroutines.launch
import java.io.ByteArrayOutputStream

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun ChatScreen(
    onNavigateToProduct: (String) -> Unit,
    onNavigateToCart: () -> Unit,
    viewModel: ChatViewModel = viewModel(),
) {
    val uiState by viewModel.uiState.collectAsStateWithLifecycle()
    val listState = rememberLazyListState()
    val snackbarHostState = remember { SnackbarHostState() }
    val scope = rememberCoroutineScope()
    val context = LocalContext.current

    // 自动滚动：新消息 → 平滑动画；流式 token 追加 → 即时滚动（避免动画堆积卡顿）
    LaunchedEffect(uiState.messages.size) {
        if (uiState.messages.isNotEmpty())
            listState.animateScrollToItem(uiState.messages.size - 1)
    }
    val lastStreamingText = (uiState.messages.lastOrNull() as? ChatMessage.AiText)
        ?.takeIf { it.isStreaming }?.text
    LaunchedEffect(lastStreamingText) {
        if (!lastStreamingText.isNullOrEmpty())
            listState.scrollToItem(uiState.messages.size - 1)
    }

    // 购物车操作 toast — 等流式响应结束后再弹，避免和转圈 loading 同时出现
    LaunchedEffect(uiState.toastMessage, uiState.isLoading) {
        if (uiState.toastMessage.isNotEmpty() && !uiState.isLoading) {
            snackbarHostState.showSnackbar(uiState.toastMessage)
            viewModel.clearToast()
        }
    }

    // pending 图片状态：选好图后先放在输入栏，等用户点发送
    var pendingImageBase64 by remember { mutableStateOf<String?>(null) }
    var pendingBitmap by remember { mutableStateOf<android.graphics.Bitmap?>(null) }

    fun setPendingImage(base64: String, bitmap: android.graphics.Bitmap) {
        pendingImageBase64 = base64
        pendingBitmap = bitmap
    }

    // 相册选择器
    val imagePickerLauncher = rememberLauncherForActivityResult(
        ActivityResultContracts.GetContent()
    ) { uri ->
        uri?.let {
            scope.launch {
                val bitmap = uriToBitmap(context, it) ?: return@launch
                val base64 = bitmapToBase64(bitmap)
                setPendingImage(base64, bitmap)
            }
        }
    }

    // 拍照：用 TakePicture + FileProvider
    var cameraImageUri by remember { mutableStateOf<Uri?>(null) }
    val cameraLauncher = rememberLauncherForActivityResult(
        ActivityResultContracts.TakePicture()
    ) { success ->
        if (success) {
            cameraImageUri?.let { uri ->
                scope.launch {
                    val bitmap = uriToBitmap(context, uri) ?: return@launch
                    val base64 = bitmapToBase64(bitmap)
                    setPendingImage(base64, bitmap)
                }
            }
        }
    }

    val cameraPermissionLauncher = rememberLauncherForActivityResult(
        ActivityResultContracts.RequestPermission()
    ) { granted ->
        if (granted) {
            val uri = createCameraUri(context)
            cameraImageUri = uri
            cameraLauncher.launch(uri)
        }
    }

    Scaffold(
        topBar = {
            TopAppBar(
                title = { Text("RAGent 导购", fontWeight = FontWeight.Bold) },
                actions = {
                    BadgedBox(
                        badge = {
                            if (uiState.cartBadgeCount > 0) {
                                Badge { Text("${uiState.cartBadgeCount}") }
                            }
                        }
                    ) {
                        IconButton(onClick = onNavigateToCart) {
                            Icon(Icons.Default.ShoppingCart, contentDescription = "购物车")
                        }
                    }
                },
                colors = TopAppBarDefaults.topAppBarColors(
                    containerColor = MaterialTheme.colorScheme.primaryContainer,
                ),
            )
        },
        snackbarHost = { SnackbarHost(snackbarHostState) },
    ) { paddingValues ->
        Column(
            modifier = Modifier
                .fillMaxSize()
                .padding(paddingValues)
                .imePadding(),
        ) {
            // 消息列表
            LazyColumn(
                state = listState,
                modifier = Modifier.weight(1f),
                contentPadding = PaddingValues(horizontal = 12.dp, vertical = 8.dp),
                verticalArrangement = Arrangement.spacedBy(8.dp),
            ) {
                items(uiState.messages) { message ->
                    MessageItem(
                        message = message,
                        onProductClick = viewModel::openProductDetail,
                        onOptionSelected = viewModel::selectClarification,
                    )
                }
            }

            // 输入栏
            ChatInputBar(
                isLoading = uiState.isLoading,
                pendingBitmap = pendingBitmap,
                onSend = { text ->
                    viewModel.sendMessage(text, pendingImageBase64, pendingBitmap)
                    pendingImageBase64 = null
                    pendingBitmap = null
                },
                onClearImage = {
                    pendingImageBase64 = null
                    pendingBitmap = null
                },
                onCameraClick = {
                    val hasPerm = ContextCompat.checkSelfPermission(
                        context, Manifest.permission.CAMERA
                    ) == PackageManager.PERMISSION_GRANTED
                    if (hasPerm) {
                        val uri = createCameraUri(context)
                        cameraImageUri = uri
                        cameraLauncher.launch(uri)
                    } else {
                        cameraPermissionLauncher.launch(Manifest.permission.CAMERA)
                    }
                },
                onGalleryClick = { imagePickerLauncher.launch("image/*") },
            )
        }
    }

    // 商品详情底部弹层
    uiState.detailProduct?.let { product ->
        ProductDetailSheet(
            product = product,
            onDismiss = viewModel::closeProductDetail,
            onAddToCart = { pid, skuId ->
                viewModel.addToCartDirect(pid, skuId, product.title)
            },
        )
    }
}

@Composable
private fun ChatInputBar(
    isLoading: Boolean,
    pendingBitmap: android.graphics.Bitmap?,
    onSend: (String) -> Unit,
    onClearImage: () -> Unit,
    onCameraClick: () -> Unit,
    onGalleryClick: () -> Unit,
) {
    var inputText by remember { mutableStateOf("") }
    val canSend = (inputText.isNotBlank() || pendingBitmap != null) && !isLoading

    Surface(shadowElevation = 8.dp) {
        Column {
            // 图片预览行（有 pending 图片时显示）
            if (pendingBitmap != null) {
                Row(
                    modifier = Modifier.padding(horizontal = 12.dp, vertical = 4.dp),
                    verticalAlignment = Alignment.CenterVertically,
                ) {
                    Box {
                        androidx.compose.foundation.Image(
                            bitmap = pendingBitmap.asImageBitmap(),
                            contentDescription = "待发送图片",
                            modifier = Modifier
                                .size(64.dp)
                                .clip(RoundedCornerShape(8.dp)),
                            contentScale = ContentScale.Crop,
                        )
                        IconButton(
                            onClick = onClearImage,
                            modifier = Modifier
                                .size(20.dp)
                                .align(Alignment.TopEnd)
                                .background(MaterialTheme.colorScheme.surface, CircleShape),
                        ) {
                            Icon(Icons.Default.Close, contentDescription = "移除图片", modifier = Modifier.size(12.dp))
                        }
                    }
                    Spacer(Modifier.width(8.dp))
                    Text("图片已选择，可输入文字后发送", style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.outline)
                }
            }
            Row(
                modifier = Modifier
                    .fillMaxWidth()
                    .padding(horizontal = 8.dp, vertical = 6.dp),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                IconButton(onClick = onCameraClick, enabled = !isLoading) {
                    Icon(Icons.Default.CameraAlt, contentDescription = "拍照找货", tint = MaterialTheme.colorScheme.primary)
                }
                IconButton(onClick = onGalleryClick, enabled = !isLoading) {
                    Icon(Icons.Default.Image, contentDescription = "从相册选图", tint = MaterialTheme.colorScheme.primary)
                }
            OutlinedTextField(
                value = inputText,
                onValueChange = { inputText = it },
                modifier = Modifier.weight(1f),
                placeholder = { Text("说点什么...") },
                maxLines = 4,
                shape = RoundedCornerShape(24.dp),
                keyboardOptions = KeyboardOptions(imeAction = ImeAction.Send),
                keyboardActions = KeyboardActions(onSend = {
                    if (canSend) {
                        onSend(inputText.trim())
                        inputText = ""
                    }
                }),
            )
            Spacer(Modifier.width(4.dp))
            IconButton(
                onClick = {
                    if (canSend) {
                        onSend(inputText.trim())
                        inputText = ""
                    }
                },
                enabled = canSend,
            ) {
                if (isLoading) {
                    CircularProgressIndicator(modifier = Modifier.size(20.dp), strokeWidth = 2.dp)
                } else {
                    Icon(Icons.AutoMirrored.Filled.Send, contentDescription = "发送", tint = MaterialTheme.colorScheme.primary)
                }
            }
            }
        }
    }
}

@Composable
private fun MessageItem(
    message: ChatMessage,
    onProductClick: (String) -> Unit,
    onOptionSelected: (String) -> Unit,
) {
    when (message) {
        is ChatMessage.User -> UserBubble(message.text, message.bitmap)
        is ChatMessage.AiText -> AiTextBubble(message.text, message.isStreaming)
        is ChatMessage.AiStatus -> StatusBubble(message.message)
        is ChatMessage.AiProductCard -> ProductCardMessage(message.product, onProductClick)
        is ChatMessage.AiProductList -> ProductCarousel(message.products, onProductClick, onOptionSelected)
        is ChatMessage.AiComparison -> ComparisonMessage(message.table, onProductClick)
        is ChatMessage.AiClarification -> ClarificationMessage(message.question, message.options, onOptionSelected)
        is ChatMessage.AiError -> ErrorBubble(message.message)
        else -> Unit
    }
}

@Composable
private fun UserBubble(text: String, bitmap: android.graphics.Bitmap? = null) {
    Box(modifier = Modifier.fillMaxWidth(), contentAlignment = Alignment.CenterEnd) {
        Column(horizontalAlignment = Alignment.End) {
            if (bitmap != null) {
                androidx.compose.foundation.Image(
                    bitmap = bitmap.asImageBitmap(),
                    contentDescription = "图片",
                    modifier = Modifier
                        .size(200.dp)
                        .clip(RoundedCornerShape(12.dp)),
                    contentScale = ContentScale.Crop,
                )
                if (text.isNotBlank()) Spacer(Modifier.height(4.dp))
            }
            if (text.isNotBlank()) {
                Surface(
                    shape = RoundedCornerShape(topStart = 16.dp, topEnd = 4.dp, bottomStart = 16.dp, bottomEnd = 16.dp),
                    color = MaterialTheme.colorScheme.primary,
                ) {
                    Text(
                        text = text,
                        modifier = Modifier
                            .widthIn(max = 280.dp)
                            .padding(horizontal = 14.dp, vertical = 10.dp),
                        color = MaterialTheme.colorScheme.onPrimary,
                    )
                }
            }
        }
    }
}

@Composable
private fun AiTextBubble(text: String, isStreaming: Boolean) {
    val displayText = if (isStreaming) "$text▌" else text
    Box(modifier = Modifier.fillMaxWidth(), contentAlignment = Alignment.CenterStart) {
        Surface(
            shape = RoundedCornerShape(topStart = 4.dp, topEnd = 16.dp, bottomStart = 16.dp, bottomEnd = 16.dp),
            color = MaterialTheme.colorScheme.surfaceVariant,
        ) {
            Text(
                text = displayText,
                modifier = Modifier
                    .widthIn(max = 300.dp)
                    .padding(horizontal = 14.dp, vertical = 10.dp),
            )
        }
    }
}

@Composable
private fun StatusBubble(message: String) {
    Row(
        modifier = Modifier.padding(vertical = 4.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        CircularProgressIndicator(modifier = Modifier.size(14.dp), strokeWidth = 2.dp)
        Spacer(Modifier.width(8.dp))
        Text(message, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.outline)
    }
}

@Composable
private fun ErrorBubble(message: String) {
    Surface(
        shape = RoundedCornerShape(8.dp),
        color = MaterialTheme.colorScheme.errorContainer,
    ) {
        Text(
            text = "⚠ $message",
            modifier = Modifier.padding(12.dp, 8.dp),
            color = MaterialTheme.colorScheme.onErrorContainer,
            style = MaterialTheme.typography.bodySmall,
        )
    }
}

// ===== 商品卡片 =====

@Composable
private fun ProductCardMessage(product: Product, onProductClick: (String) -> Unit) {
    Card(
        modifier = Modifier
            .fillMaxWidth()
            .clickable { onProductClick(product.productId) },
        elevation = CardDefaults.cardElevation(defaultElevation = 2.dp),
    ) {
        Row(modifier = Modifier.padding(10.dp)) {
            AsyncImage(
                model = NetworkConfig.imageUrl(product.imageUrl),
                contentDescription = product.title,
                modifier = Modifier
                    .size(80.dp)
                    .clip(RoundedCornerShape(8.dp)),
                contentScale = ContentScale.Crop,
            )
            Spacer(Modifier.width(10.dp))
            Column(modifier = Modifier.weight(1f)) {
                Text(
                    product.brand,
                    style = MaterialTheme.typography.labelSmall,
                    color = MaterialTheme.colorScheme.outline,
                )
                Spacer(Modifier.height(2.dp))
                Text(
                    product.title,
                    style = MaterialTheme.typography.bodyMedium,
                    fontWeight = FontWeight.Medium,
                    maxLines = 2,
                    overflow = TextOverflow.Ellipsis,
                )
                Spacer(Modifier.height(4.dp))
                Text(
                    "¥%.2f 起".format(product.displayPrice),
                    color = MaterialTheme.colorScheme.error,
                    fontWeight = FontWeight.Bold,
                )
                product.reason?.let {
                    Spacer(Modifier.height(4.dp))
                    Text(it, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.outline, maxLines = 2, overflow = TextOverflow.Ellipsis)
                }
            }
        }
    }
}

@Composable
private fun ProductCarousel(
    products: List<Product>,
    onProductClick: (String) -> Unit,
    onOptionSelected: (String) -> Unit,
) {
    if (products.isEmpty()) return
    val pagerState = rememberPagerState(pageCount = { products.size })
    Column(modifier = Modifier.fillMaxWidth()) {
        HorizontalPager(
            state = pagerState,
            contentPadding = PaddingValues(horizontal = 8.dp),
            pageSpacing = 12.dp,
            modifier = Modifier.fillMaxWidth(),
        ) { page ->
            ProductCardLarge(products[page]) { onProductClick(products[page].productId) }
        }

        Spacer(Modifier.height(10.dp))

        // 页码指示点
        Row(
            modifier = Modifier.fillMaxWidth(),
            horizontalArrangement = Arrangement.Center,
            verticalAlignment = Alignment.CenterVertically,
        ) {
            repeat(products.size) { i ->
                val selected = i == pagerState.currentPage
                Box(
                    modifier = Modifier
                        .padding(horizontal = 3.dp)
                        .size(if (selected) 8.dp else 6.dp)
                        .clip(CircleShape)
                        .background(
                            if (selected) MaterialTheme.colorScheme.primary
                            else MaterialTheme.colorScheme.outline.copy(alpha = 0.4f)
                        )
                )
            }
        }

        // 滑到最后一页时淡入"重新搜索"提示
        AnimatedVisibility(
            visible = pagerState.currentPage == products.size - 1 && products.size > 1,
            enter = fadeIn() + expandVertically(),
            exit = fadeOut() + shrinkVertically(),
        ) {
            Row(
                modifier = Modifier
                    .fillMaxWidth()
                    .padding(top = 10.dp),
                horizontalArrangement = Arrangement.Center,
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Text(
                    "没找到合适的？",
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.outline,
                )
                TextButton(
                    onClick = { onOptionSelected("都不是，换个方式找") },
                    contentPadding = PaddingValues(horizontal = 8.dp, vertical = 0.dp),
                ) {
                    Text("重新搜索", style = MaterialTheme.typography.bodySmall)
                }
            }
        }
    }
}

@Composable
private fun ProductCardLarge(product: Product, onClick: () -> Unit) {
    Card(
        modifier = Modifier
            .fillMaxWidth()
            .clickable { onClick() },
        elevation = CardDefaults.cardElevation(defaultElevation = 4.dp),
        shape = RoundedCornerShape(20.dp),
    ) {
        Column {
            AsyncImage(
                model = NetworkConfig.imageUrl(product.imageUrl),
                contentDescription = product.title,
                modifier = Modifier
                    .fillMaxWidth()
                    .height(200.dp),
                contentScale = ContentScale.Crop,
            )
            Column(modifier = Modifier.padding(16.dp)) {
                Text(
                    product.brand,
                    style = MaterialTheme.typography.labelSmall,
                    color = MaterialTheme.colorScheme.outline,
                )
                Spacer(Modifier.height(2.dp))
                Text(
                    product.title,
                    style = MaterialTheme.typography.bodyLarge,
                    fontWeight = FontWeight.SemiBold,
                    maxLines = 2,
                    overflow = TextOverflow.Ellipsis,
                )
                Spacer(Modifier.height(8.dp))
                Text(
                    "¥%.2f 起".format(product.displayPrice),
                    style = MaterialTheme.typography.titleMedium,
                    color = MaterialTheme.colorScheme.error,
                    fontWeight = FontWeight.Bold,
                )
            }
        }
    }
}

// ===== 商品对比表格 =====

@Composable
private fun ComparisonMessage(table: ComparisonTable, onProductClick: (String) -> Unit) {
    Card(modifier = Modifier.fillMaxWidth(), elevation = CardDefaults.cardElevation(defaultElevation = 2.dp)) {
        Column(modifier = Modifier.padding(12.dp)) {
            Text("商品对比", style = MaterialTheme.typography.titleSmall, fontWeight = FontWeight.Bold)
            Spacer(Modifier.height(8.dp))

            // 商品图片行
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                Spacer(Modifier.width(80.dp))
                table.products.forEach { product ->
                    Column(
                        modifier = Modifier
                            .weight(1f)
                            .clickable { onProductClick(product.productId) },
                        horizontalAlignment = Alignment.CenterHorizontally,
                    ) {
                        AsyncImage(
                            model = NetworkConfig.imageUrl(product.imageUrl),
                            contentDescription = product.title,
                            modifier = Modifier.size(56.dp).clip(RoundedCornerShape(4.dp)),
                            contentScale = ContentScale.Crop,
                        )
                        Text(product.title, style = MaterialTheme.typography.labelSmall, maxLines = 1, overflow = TextOverflow.Ellipsis)
                        Text("¥%.0f".format(product.displayPrice), style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.error)
                    }
                }
            }

            Spacer(Modifier.height(8.dp))

            // 对比维度
            table.dimensions.forEach { dim ->
                Row(modifier = Modifier.padding(vertical = 3.dp)) {
                    Text(dim.name, style = MaterialTheme.typography.bodySmall, fontWeight = FontWeight.Medium, modifier = Modifier.width(80.dp))
                    dim.values.forEachIndexed { i, value ->
                        Text(value, style = MaterialTheme.typography.bodySmall, modifier = Modifier.weight(1f), maxLines = 2, overflow = TextOverflow.Ellipsis)
                    }
                }
            }

            // 推荐理由
            table.recommendation?.let { rec ->
                Spacer(Modifier.height(8.dp))
                Surface(color = MaterialTheme.colorScheme.primaryContainer, shape = RoundedCornerShape(8.dp)) {
                    Text(rec.reason, style = MaterialTheme.typography.bodySmall, modifier = Modifier.padding(8.dp))
                }
            }
        }
    }
}

// ===== 反问选项 =====

@Composable
private fun ClarificationMessage(question: String, options: List<String>, onSelected: (String) -> Unit) {
    Column {
        AiTextBubble(question, isStreaming = false)
        Spacer(Modifier.height(8.dp))
        Column(verticalArrangement = Arrangement.spacedBy(6.dp)) {
            options.forEach { option ->
                OutlinedButton(
                    onClick = { onSelected(option) },
                    modifier = Modifier.fillMaxWidth(),
                    shape = RoundedCornerShape(10.dp),
                    contentPadding = PaddingValues(horizontal = 16.dp, vertical = 10.dp),
                ) {
                    Text(
                        option,
                        style = MaterialTheme.typography.bodyMedium,
                        textAlign = TextAlign.Center,
                    )
                }
            }
        }
    }
}

// ===== 商品详情底部面板 =====

@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun ProductDetailSheet(
    product: Product,
    onDismiss: () -> Unit,
    onAddToCart: (productId: String, skuId: String) -> Unit,
) {
    val sheetState = rememberModalBottomSheetState(skipPartiallyExpanded = true)
    val scope = rememberCoroutineScope()

    // 维护用户在每个属性上的选择，例如 {颜色=黑色, 尺码=M}
    val propertyKeys = remember(product) {
        product.skus.flatMap { it.properties.keys }.distinct()
    }
    var selectedProps by remember(product) {
        mutableStateOf(product.skus.firstOrNull()?.properties ?: emptyMap())
    }

    // 根据当前选择匹配 SKU；找不到完全匹配时降级为第一个 SKU
    val matchedSku = remember(product, selectedProps) {
        product.skus.firstOrNull { it.properties == selectedProps }
            ?: product.skus.firstOrNull()
    }

    ModalBottomSheet(
        onDismissRequest = onDismiss,
        sheetState = sheetState,
        containerColor = MaterialTheme.colorScheme.surface,
        dragHandle = null,   // 自定义视觉，不要默认拖动条
    ) {
        Column(modifier = Modifier.fillMaxWidth()) {

            // ── Hero 图：全宽满铺，无圆角，底部带柔性渐变 ──
            Box {
                AsyncImage(
                    model = NetworkConfig.imageUrl(product.imageUrl),
                    contentDescription = product.title,
                    modifier = Modifier
                        .fillMaxWidth()
                        .height(300.dp),
                    contentScale = ContentScale.Crop,
                )
                // 顶部一个细拖动条（视觉提示可下滑关闭）
                Box(
                    modifier = Modifier
                        .align(Alignment.TopCenter)
                        .padding(top = 12.dp)
                        .width(36.dp)
                        .height(4.dp)
                        .clip(RoundedCornerShape(2.dp))
                        .background(Color.White.copy(alpha = 0.6f))
                )
            }

            // ── 内容区，宽边距 ──
            Column(
                modifier = Modifier
                    .fillMaxWidth()
                    .padding(horizontal = 24.dp, vertical = 22.dp),
            ) {

                // 品牌
                Text(
                    product.brand,
                    style = MaterialTheme.typography.labelLarge,
                    color = MaterialTheme.colorScheme.outline,
                    letterSpacing = 1.sp,
                )

                Spacer(Modifier.height(6.dp))

                // 商品名（大字粗体）
                Text(
                    product.title,
                    style = MaterialTheme.typography.titleLarge,
                    fontWeight = FontWeight.Bold,
                    lineHeight = 28.sp,
                )

                Spacer(Modifier.height(16.dp))

                // 价格（超大号红字）
                Row(verticalAlignment = Alignment.Bottom) {
                    Text(
                        "¥",
                        fontSize = 18.sp,
                        color = MaterialTheme.colorScheme.error,
                        fontWeight = FontWeight.Bold,
                        modifier = Modifier.padding(bottom = 4.dp),
                    )
                    Text(
                        "%.2f".format(matchedSku?.price ?: product.displayPrice),
                        fontSize = 32.sp,
                        color = MaterialTheme.colorScheme.error,
                        fontWeight = FontWeight.Bold,
                        lineHeight = 36.sp,
                    )
                }

                Spacer(Modifier.height(20.dp))
                HorizontalDivider(color = MaterialTheme.colorScheme.outlineVariant.copy(alpha = 0.6f))
                Spacer(Modifier.height(18.dp))

                // ── SKU 选择 ──
                propertyKeys.forEach { key ->
                    val values = product.skus.mapNotNull { it.properties[key] }.distinct()
                    Row(
                        verticalAlignment = Alignment.CenterVertically,
                        modifier = Modifier.fillMaxWidth(),
                    ) {
                        Text(
                            key,
                            style = MaterialTheme.typography.bodyMedium,
                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                            modifier = Modifier.width(48.dp),
                        )
                        Row(
                            horizontalArrangement = Arrangement.spacedBy(8.dp),
                            modifier = Modifier.weight(1f),
                        ) {
                            values.forEach { v ->
                                val selected = selectedProps[key] == v
                                FilterChip(
                                    selected = selected,
                                    onClick = { selectedProps = selectedProps + (key to v) },
                                    label = {
                                        Text(
                                            v,
                                            style = MaterialTheme.typography.bodyMedium,
                                            fontWeight = if (selected) FontWeight.SemiBold else FontWeight.Normal,
                                        )
                                    },
                                    shape = RoundedCornerShape(10.dp),
                                    colors = FilterChipDefaults.filterChipColors(
                                        selectedContainerColor = MaterialTheme.colorScheme.primary.copy(alpha = 0.15f),
                                        selectedLabelColor = MaterialTheme.colorScheme.primary,
                                    ),
                                )
                            }
                        }
                    }
                    Spacer(Modifier.height(14.dp))
                }

                // ── 商品介绍 ──
                product.marketingDescription?.takeIf { it.isNotBlank() }?.let { desc ->
                    Spacer(Modifier.height(4.dp))
                    HorizontalDivider(color = MaterialTheme.colorScheme.outlineVariant.copy(alpha = 0.6f))
                    Spacer(Modifier.height(18.dp))
                    Text(
                        "商品介绍",
                        style = MaterialTheme.typography.titleSmall,
                        fontWeight = FontWeight.SemiBold,
                    )
                    Spacer(Modifier.height(10.dp))
                    Text(
                        desc,
                        style = MaterialTheme.typography.bodyMedium,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                        lineHeight = 22.sp,
                        maxLines = 6,
                        overflow = TextOverflow.Ellipsis,
                    )
                    Spacer(Modifier.height(24.dp))
                }

                // ── 操作按钮（大尺寸圆角胶囊形）──
                Row(
                    modifier = Modifier.fillMaxWidth(),
                    horizontalArrangement = Arrangement.spacedBy(12.dp),
                ) {
                    OutlinedButton(
                        onClick = {
                            scope.launch {
                                sheetState.hide()
                                onDismiss()
                            }
                        },
                        modifier = Modifier
                            .weight(1f)
                            .height(54.dp),
                        shape = RoundedCornerShape(27.dp),
                    ) {
                        Text(
                            "继续看看",
                            style = MaterialTheme.typography.titleSmall,
                        )
                    }
                    Button(
                        onClick = {
                            matchedSku?.let { onAddToCart(product.productId, it.skuId) }
                        },
                        modifier = Modifier
                            .weight(1.4f)
                            .height(54.dp),
                        shape = RoundedCornerShape(27.dp),
                        enabled = matchedSku != null,
                    ) {
                        Text(
                            "加入购物车",
                            style = MaterialTheme.typography.titleMedium,
                            fontWeight = FontWeight.Bold,
                        )
                    }
                }
            }
        }
    }
}

// ===== 工具函数 =====

private fun bitmapToBase64(bitmap: Bitmap): String {
    val output = ByteArrayOutputStream()
    val scaled = if (bitmap.width > 800 || bitmap.height > 800) {
        val ratio = minOf(800f / bitmap.width, 800f / bitmap.height)
        Bitmap.createScaledBitmap(bitmap, (bitmap.width * ratio).toInt(), (bitmap.height * ratio).toInt(), true)
    } else bitmap
    scaled.compress(Bitmap.CompressFormat.JPEG, 80, output)
    return Base64.encodeToString(output.toByteArray(), Base64.NO_WRAP)
}

private fun uriToBitmap(context: android.content.Context, uri: Uri): Bitmap? {
    return try {
        context.contentResolver.openInputStream(uri)?.use { input ->
            android.graphics.BitmapFactory.decodeStream(input)
        }
    } catch (e: Exception) {
        null
    }
}

private fun uriToBase64(context: android.content.Context, uri: Uri): String? {
    return try {
        context.contentResolver.openInputStream(uri)?.use { input ->
            val bitmap = android.graphics.BitmapFactory.decodeStream(input) ?: return null
            bitmapToBase64(bitmap)
        }
    } catch (e: Exception) {
        null
    }
}

private fun createCameraUri(context: android.content.Context): Uri {
    val dir = File(context.cacheDir, "camera").also { it.mkdirs() }
    val file = File.createTempFile("photo_", ".jpg", dir)
    return FileProvider.getUriForFile(context, "${context.packageName}.fileprovider", file)
}
