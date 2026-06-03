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
import androidx.compose.animation.core.FastOutSlowInEasing
import androidx.compose.animation.core.LinearEasing
import androidx.compose.animation.core.RepeatMode
import androidx.compose.animation.core.animateFloat
import androidx.compose.animation.core.infiniteRepeatable
import androidx.compose.animation.core.rememberInfiniteTransition
import androidx.compose.animation.core.tween
import androidx.compose.animation.expandVertically
import androidx.compose.animation.fadeIn
import androidx.compose.animation.fadeOut
import androidx.compose.animation.shrinkVertically
import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.WindowInsets
import androidx.compose.foundation.layout.fillMaxHeight
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
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.IntrinsicSize
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.foundation.pager.rememberPagerState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.Send
import androidx.compose.material.icons.filled.Add
import androidx.compose.material.icons.filled.AddShoppingCart
import androidx.compose.material.icons.filled.CameraAlt
import androidx.compose.material.icons.filled.ChatBubbleOutline
import androidx.compose.material.icons.filled.Close
import androidx.compose.material.icons.filled.Image
import androidx.compose.material.icons.filled.Menu
import androidx.compose.material.icons.filled.ReceiptLong
import androidx.compose.material.icons.filled.ShoppingCart
import androidx.compose.material3.Badge
import androidx.compose.material3.BadgedBox
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.FilterChip
import androidx.compose.material3.FilterChipDefaults
import androidx.compose.material3.DrawerValue
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.VerticalDivider
import androidx.compose.material3.ModalBottomSheet
import androidx.compose.material3.ModalDrawerSheet
import androidx.compose.material3.ModalNavigationDrawer
import androidx.compose.material3.NavigationDrawerItem
import androidx.compose.material3.NavigationDrawerItemDefaults
import androidx.compose.material3.rememberDrawerState
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.OutlinedTextFieldDefaults
import androidx.compose.material3.rememberModalBottomSheetState
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
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
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.graphics.graphicsLayer
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
import com.ragent.shopping.data.model.SessionSummary
import com.ragent.shopping.data.remote.NetworkConfig
import com.ragent.shopping.ui.theme.BrandIndigo
import com.ragent.shopping.ui.theme.BrandSky
import com.ragent.shopping.ui.theme.BrandViolet
import com.ragent.shopping.ui.viewmodel.ChatViewModel
import kotlinx.coroutines.launch
import java.io.ByteArrayOutputStream

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun ChatScreen(
    onNavigateToProduct: (String) -> Unit,
    onNavigateToCart: () -> Unit,
    onNavigateToOrders: () -> Unit = {},
    viewModel: ChatViewModel = viewModel(),
) {
    val uiState by viewModel.uiState.collectAsStateWithLifecycle()
    val listState = rememberLazyListState()
    val snackbarHostState = remember { SnackbarHostState() }
    val scope = rememberCoroutineScope()
    val context = LocalContext.current
    val drawerState = rememberDrawerState(DrawerValue.Closed)

    // 打开抽屉时刷新会话列表
    LaunchedEffect(drawerState.currentValue) {
        if (drawerState.currentValue == DrawerValue.Open) viewModel.refreshSessions()
    }

    // 自动滚动：ViewModel 每次有新内容出现就 +1 scrollTick，这里统一响应
    LaunchedEffect(uiState.scrollTick) {
        if (uiState.scrollTick > 0 && uiState.messages.isNotEmpty())
            listState.animateScrollToItem(uiState.messages.size - 1)
    }
    // 流式 token 逐字追加时即时跟滚（避免动画堆积卡顿）
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

    ModalNavigationDrawer(
        drawerState = drawerState,
        drawerContent = {
            SessionDrawer(
                sessions = uiState.sessions,
                currentId = uiState.sessionId,
                onNewSession = {
                    viewModel.newSession()
                    scope.launch { drawerState.close() }
                },
                onSwitch = { id ->
                    viewModel.switchSession(id)
                    scope.launch { drawerState.close() }
                },
            )
        },
    ) {
    Scaffold(
        topBar = {
            Box(
                modifier = Modifier
                    .fillMaxWidth()
                    .background(
                        Brush.horizontalGradient(
                            colors = listOf(BrandIndigo, BrandViolet, Color(0xFF7C4DFF)),
                        )
                    )
            ) {
                TopAppBar(
                    title = {
                        Text(
                            "RAGent 导购",
                            fontWeight = FontWeight.Bold,
                            color = Color.White,
                        )
                    },
                    navigationIcon = {
                        IconButton(onClick = { scope.launch { drawerState.open() } }) {
                            Icon(Icons.Default.Menu, contentDescription = "会话列表", tint = Color.White)
                        }
                    },
                    actions = {
                        IconButton(onClick = onNavigateToOrders) {
                            Icon(
                                Icons.Default.ReceiptLong,
                                contentDescription = "我的订单",
                                tint = Color.White,
                            )
                        }
                        BadgedBox(
                            badge = {
                                if (uiState.cartBadgeCount > 0) {
                                    Badge { Text("${uiState.cartBadgeCount}") }
                                }
                            }
                        ) {
                            IconButton(onClick = onNavigateToCart) {
                                Icon(
                                    Icons.Default.ShoppingCart,
                                    contentDescription = "购物车",
                                    tint = Color.White,
                                )
                            }
                        }
                    },
                    colors = TopAppBarDefaults.topAppBarColors(
                        containerColor = Color.Transparent,
                    ),
                )
            }
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
                contentPadding = PaddingValues(vertical = 8.dp),
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
    }  // ModalNavigationDrawer

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

    // 收货信息表单底部弹层（下单流程）
    if (uiState.showOrderForm) {
        OrderFormBottomSheet(
            savedAddresses = uiState.orderFormAddresses,
            initialName    = uiState.lastUsedName,
            initialPhone   = uiState.lastUsedPhone,
            initialAddress = uiState.lastUsedAddress,
            onSubmit = { name, phone, address ->
                viewModel.submitOrderForm(name, phone, address)
            },
            onDismiss = viewModel::dismissOrderForm,
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

    // 流光渐变：发送按钮
    val sendTransition = rememberInfiniteTransition(label = "send_gradient")
    val sendShift by sendTransition.animateFloat(
        initialValue = -400f,
        targetValue = 400f,
        animationSpec = infiniteRepeatable(
            animation = tween(2000, easing = LinearEasing),
            repeatMode = RepeatMode.Restart,
        ),
        label = "send_shift",
    )
    val sendGradient = Brush.linearGradient(
        colors = listOf(BrandIndigo, BrandViolet, BrandSky, BrandIndigo),
        start = Offset(sendShift, 0f),
        end = Offset(sendShift + 400f, 200f),
    )

    Column(modifier = Modifier.padding(start = 12.dp, end = 12.dp, bottom = 12.dp, top = 6.dp)) {
        // 图片预览行
        if (pendingBitmap != null) {
            Row(
                modifier = Modifier.padding(bottom = 6.dp),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Box {
                    androidx.compose.foundation.Image(
                        bitmap = pendingBitmap.asImageBitmap(),
                        contentDescription = "待发送图片",
                        modifier = Modifier
                            .size(64.dp)
                            .clip(RoundedCornerShape(10.dp)),
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
                Text(
                    "图片已选择，可输入文字后发送",
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.outline,
                )
            }
        }

        // 输入卡片（豆包风格浮动胶囊）
        Surface(
            shape = RoundedCornerShape(28.dp),
            shadowElevation = 10.dp,
            color = MaterialTheme.colorScheme.surface,
        ) {
            Row(
                modifier = Modifier
                    .fillMaxWidth()
                    .padding(horizontal = 6.dp, vertical = 4.dp),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                IconButton(onClick = onCameraClick, enabled = !isLoading, modifier = Modifier.size(36.dp)) {
                    Icon(
                        Icons.Default.CameraAlt,
                        contentDescription = "拍照找货",
                        tint = MaterialTheme.colorScheme.onSurfaceVariant,
                        modifier = Modifier.size(20.dp),
                    )
                }
                IconButton(onClick = onGalleryClick, enabled = !isLoading, modifier = Modifier.size(36.dp)) {
                    Icon(
                        Icons.Default.Image,
                        contentDescription = "从相册选图",
                        tint = MaterialTheme.colorScheme.onSurfaceVariant,
                        modifier = Modifier.size(20.dp),
                    )
                }
                OutlinedTextField(
                    value = inputText,
                    onValueChange = { inputText = it },
                    modifier = Modifier.weight(1f),
                    placeholder = {
                        Text(
                            "说点什么...",
                            style = MaterialTheme.typography.bodyMedium,
                            color = MaterialTheme.colorScheme.outline,
                        )
                    },
                    maxLines = 4,
                    colors = OutlinedTextFieldDefaults.colors(
                        focusedBorderColor    = Color.Transparent,
                        unfocusedBorderColor  = Color.Transparent,
                        focusedContainerColor = Color.Transparent,
                        unfocusedContainerColor = Color.Transparent,
                    ),
                    textStyle = MaterialTheme.typography.bodyMedium,
                    keyboardOptions = KeyboardOptions(imeAction = ImeAction.Send),
                    keyboardActions = KeyboardActions(onSend = {
                        if (canSend) { onSend(inputText.trim()); inputText = "" }
                    }),
                )
                Spacer(Modifier.width(4.dp))
                Box(
                    modifier = Modifier
                        .size(40.dp)
                        .clip(CircleShape)
                        .background(if (canSend) sendGradient else Brush.linearGradient(
                            listOf(
                                MaterialTheme.colorScheme.outline.copy(alpha = 0.3f),
                                MaterialTheme.colorScheme.outline.copy(alpha = 0.3f),
                            )
                        ))
                        .clickable(enabled = canSend) { onSend(inputText.trim()); inputText = "" },
                    contentAlignment = Alignment.Center,
                ) {
                    if (isLoading) {
                        CircularProgressIndicator(
                            modifier = Modifier.size(18.dp),
                            strokeWidth = 2.dp,
                            color = Color.White,
                        )
                    } else {
                        Icon(
                            Icons.AutoMirrored.Filled.Send,
                            contentDescription = "发送",
                            tint = Color.White,
                            modifier = Modifier.size(18.dp),
                        )
                    }
                }
                Spacer(Modifier.width(4.dp))
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
    // AiProductList 需要全宽（自带内边距），其他消息统一加 12dp 水平 padding
    val mod = if (message is ChatMessage.AiProductList) Modifier
    else Modifier.padding(horizontal = 12.dp)

    Box(modifier = mod) {
        when (message) {
            is ChatMessage.User -> UserBubble(message.text, message.bitmap)
            is ChatMessage.AiText -> AiTextBubble(message.text, message.isStreaming)
            is ChatMessage.AiStatus -> StatusBubble(message.message)
            is ChatMessage.AiProductCard -> ProductCardMessage(message.product, onProductClick)
            is ChatMessage.AiProductList -> ProductCarousel(message.products, message.searchType, onProductClick, onOptionSelected)
            is ChatMessage.AiComparison -> ComparisonMessage(message.table, onProductClick)
            is ChatMessage.AiClarification -> ClarificationMessage(message.question, message.options, onOptionSelected)
            is ChatMessage.AiError -> ErrorBubble(message.message)
            else -> Unit
        }
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
                Box(
                    modifier = Modifier
                        .widthIn(max = 280.dp)
                        .clip(RoundedCornerShape(topStart = 16.dp, topEnd = 4.dp, bottomStart = 16.dp, bottomEnd = 16.dp))
                        .background(
                            Brush.linearGradient(
                                colors = listOf(BrandIndigo, BrandViolet),
                                start = Offset(0f, 0f),
                                end = Offset(Float.POSITIVE_INFINITY, Float.POSITIVE_INFINITY),
                            )
                        )
                        .padding(horizontal = 14.dp, vertical = 10.dp),
                ) {
                    Text(text = text, color = Color.White, style = MaterialTheme.typography.bodyMedium)
                }
            }
        }
    }
}

@Composable
private fun AiTextBubble(text: String, isStreaming: Boolean) {
    // 流式光标加在末尾最后一个非空行
    val displayText = if (isStreaming) "$text▌" else text
    Box(modifier = Modifier.fillMaxWidth(), contentAlignment = Alignment.CenterStart) {
        Surface(
            shape = RoundedCornerShape(topStart = 4.dp, topEnd = 16.dp, bottomStart = 16.dp, bottomEnd = 16.dp),
            color = Color(0xFFF2F2F7),
            shadowElevation = 0.dp,
        ) {
            MarkdownContent(
                text = displayText,
                modifier = Modifier
                    .fillMaxWidth()
                    .padding(horizontal = 14.dp, vertical = 10.dp),
                baseStyle = MaterialTheme.typography.bodyMedium,
                baseColor = Color(0xFF1A1A1A),
            )
        }
    }
}

@Composable
private fun StatusBubble(message: String) {
    Box(modifier = Modifier.fillMaxWidth(), contentAlignment = Alignment.CenterStart) {
        Surface(
            shape = RoundedCornerShape(topStart = 4.dp, topEnd = 16.dp, bottomStart = 16.dp, bottomEnd = 16.dp),
            color = MaterialTheme.colorScheme.surface,
            shadowElevation = 2.dp,
            border = BorderStroke(1.dp, MaterialTheme.colorScheme.outlineVariant),
        ) {
            Row(
                modifier = Modifier.padding(horizontal = 14.dp, vertical = 12.dp),
                verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.spacedBy(5.dp),
            ) {
                val transition = rememberInfiniteTransition(label = "typing")
                repeat(3) { i ->
                    val scale by transition.animateFloat(
                        initialValue = 0.5f,
                        targetValue = 1f,
                        animationSpec = infiniteRepeatable(
                            animation = tween(500, delayMillis = i * 160, easing = FastOutSlowInEasing),
                            repeatMode = RepeatMode.Reverse,
                        ),
                        label = "dot_$i",
                    )
                    Box(
                        modifier = Modifier
                            .size(7.dp)
                            .graphicsLayer { scaleX = scale; scaleY = scale }
                            .clip(CircleShape)
                            .background(BrandIndigo.copy(alpha = 0.4f + 0.6f * scale)),
                    )
                }
                Spacer(Modifier.width(6.dp))
                Text(
                    message,
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
        }
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
    searchType: String,
    onProductClick: (String) -> Unit,
    onOptionSelected: (String) -> Unit,
) {
    if (products.isEmpty()) return
    val pagerState = rememberPagerState(pageCount = { products.size })
    Column(modifier = Modifier.fillMaxWidth()) {
        HorizontalPager(
            state = pagerState,
            modifier = Modifier.fillMaxWidth(),
            contentPadding = PaddingValues(horizontal = 16.dp),
            pageSpacing = 12.dp,
        ) { page ->
            ProductCardLarge(products[page], searchType) { onProductClick(products[page].productId) }
        }

        Spacer(Modifier.height(10.dp))

        // 页码指示点（当前页拉伸为胶囊形）
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
                        .then(
                            if (selected) Modifier.size(width = 20.dp, height = 6.dp)
                            else Modifier.size(6.dp)
                        )
                        .clip(RoundedCornerShape(3.dp))
                        .background(
                            if (selected) BrandIndigo
                            else MaterialTheme.colorScheme.outline.copy(alpha = 0.3f)
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
private fun ProductCardLarge(product: Product, searchType: String = "text", onClick: () -> Unit) {
    Card(
        modifier = Modifier
            .fillMaxWidth()
            .clickable { onClick() },
        elevation = CardDefaults.cardElevation(defaultElevation = 4.dp),
        shape = RoundedCornerShape(20.dp),
    ) {
        Column {
            // 图片区：用 Fit 显示完整商品图，不裁剪；背景色补白边
            Box(
                modifier = Modifier
                    .fillMaxWidth()
                    .height(220.dp)
                    .background(MaterialTheme.colorScheme.surfaceVariant),
                contentAlignment = Alignment.Center,
            ) {
                AsyncImage(
                    model = NetworkConfig.imageUrl(product.imageUrl),
                    contentDescription = product.title,
                    modifier = Modifier.fillMaxSize(),
                    contentScale = ContentScale.Fit,
                )
                // 左上角来源角标：所有模式统一样式，只有文案不同
                val badgeText = if (searchType == "image") "📷 图搜推荐" else "✦ AI 精选"
                val badgeColors = if (searchType == "image")
                    listOf(BrandViolet, BrandSky)
                else
                    listOf(BrandIndigo, BrandViolet)
                Box(
                    modifier = Modifier
                        .align(Alignment.TopStart)
                        .padding(10.dp)
                        .clip(RoundedCornerShape(8.dp))
                        .background(Brush.linearGradient(badgeColors))
                        .padding(horizontal = 8.dp, vertical = 4.dp),
                ) {
                    Text(
                        badgeText,
                        style = MaterialTheme.typography.labelSmall,
                        color = Color.White,
                        fontWeight = FontWeight.Bold,
                    )
                }
            }
            Column(modifier = Modifier.padding(horizontal = 16.dp, vertical = 12.dp)) {
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
                Row(
                    modifier = Modifier.fillMaxWidth(),
                    horizontalArrangement = Arrangement.SpaceBetween,
                    verticalAlignment = Alignment.CenterVertically,
                ) {
                    Text(
                        "¥%.2f 起".format(product.displayPrice),
                        style = MaterialTheme.typography.titleMedium,
                        color = MaterialTheme.colorScheme.error,
                        fontWeight = FontWeight.Bold,
                    )
                    Box(
                        modifier = Modifier
                            .clip(RoundedCornerShape(10.dp))
                            .background(
                                Brush.linearGradient(listOf(BrandIndigo, BrandViolet))
                            )
                            .padding(horizontal = 10.dp, vertical = 4.dp),
                    ) {
                        Text(
                            "查看详情 ›",
                            style = MaterialTheme.typography.labelSmall,
                            color = Color.White,
                            fontWeight = FontWeight.Medium,
                        )
                    }
                }
            }
        }
    }
}

// ===== 商品对比表格 =====

@Composable
private fun ordinalMark(index: Int): String =
    listOf("①", "②", "③", "④", "⑤").getOrElse(index) { "${index + 1}." }

@Composable
private fun ComparisonMessage(table: ComparisonTable, onProductClick: (String) -> Unit) {
    val dimColWidth   = 68.dp
    val productColWidth = 130.dp   // 固定宽度：多款时横向滚动，不再截断
    val borderColor   = Color(0xFFDDDDDD)
    val scrollState   = rememberScrollState()

    Column(modifier = Modifier.fillMaxWidth()) {
        Card(
            modifier = Modifier.fillMaxWidth(),
            elevation = CardDefaults.cardElevation(defaultElevation = 0.dp),
            border = BorderStroke(1.dp, borderColor),
            colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.background),
        ) {
            // horizontalScroll 让表格超宽时可左右滑动
            Column(modifier = Modifier.horizontalScroll(scrollState)) {

                // ── 表头行 ──────────────────────────────────
                Row(modifier = Modifier.height(IntrinsicSize.Min)) {
                    Box(
                        modifier = Modifier
                            .width(dimColWidth)
                            .fillMaxHeight()
                            .padding(horizontal = 8.dp, vertical = 10.dp),
                        contentAlignment = Alignment.CenterStart,
                    ) {
                        Text(
                            "对比",
                            style = MaterialTheme.typography.labelMedium,
                            fontWeight = FontWeight.Bold,
                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                        )
                    }
                    table.products.forEachIndexed { idx, product ->
                        VerticalDivider(color = borderColor)
                        Box(
                            modifier = Modifier
                                .width(productColWidth)
                                .fillMaxHeight()
                                .clickable { onProductClick(product.productId) }
                                .padding(horizontal = 10.dp, vertical = 10.dp),
                            contentAlignment = Alignment.TopStart,
                        ) {
                            Text(
                                "${ordinalMark(idx)} ${product.title}",
                                style = MaterialTheme.typography.bodySmall,
                                fontWeight = FontWeight.SemiBold,
                                lineHeight = 18.sp,
                            )
                        }
                    }
                }
                HorizontalDivider(color = borderColor)

                // ── 维度行 ──────────────────────────────────
                table.dimensions.forEachIndexed { dimIdx, dim ->
                    Row(modifier = Modifier.height(IntrinsicSize.Min)) {
                        // 维度名称列（固定宽度，居中对齐）
                        Box(
                            modifier = Modifier
                                .width(dimColWidth)
                                .fillMaxHeight()
                                .background(MaterialTheme.colorScheme.surfaceVariant.copy(alpha = 0.5f))
                                .padding(horizontal = 8.dp, vertical = 10.dp),
                            contentAlignment = Alignment.CenterStart,
                        ) {
                            Text(
                                dim.name,
                                style = MaterialTheme.typography.labelSmall,
                                fontWeight = FontWeight.SemiBold,
                                color = MaterialTheme.colorScheme.onSurfaceVariant,
                            )
                        }
                        // 各商品值列（无 maxLines，内容完整展示）
                        table.products.forEachIndexed { idx, _ ->
                            VerticalDivider(color = borderColor)
                            Box(
                                modifier = Modifier
                                    .width(productColWidth)
                                    .fillMaxHeight()
                                    .padding(horizontal = 10.dp, vertical = 10.dp),
                                contentAlignment = Alignment.TopStart,
                            ) {
                                Text(
                                    dim.values.getOrElse(idx) { "—" },
                                    style = MaterialTheme.typography.bodySmall,
                                    lineHeight = 18.sp,
                                )
                            }
                        }
                    }
                    if (dimIdx < table.dimensions.size - 1) HorizontalDivider(color = borderColor)
                }
            }
        }

        // 滚动提示（有多款商品且内容超出屏幕时显示）
        if (table.products.size >= 3) {
            Spacer(Modifier.height(4.dp))
            Text(
                "← 左右滑动查看全部对比 →",
                style = MaterialTheme.typography.labelSmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant.copy(alpha = 0.6f),
                modifier = Modifier.fillMaxWidth(),
                textAlign = androidx.compose.ui.text.style.TextAlign.Center,
            )
        }

        // 推荐理由
        table.recommendation?.let { rec ->
            Spacer(Modifier.height(6.dp))
            val recName = table.products.firstOrNull { it.productId == rec.productId }?.title
            val recText = if (recName != null) {
                "综合来看，更推荐「$recName」：${rec.reason}"
            } else {
                "综合来看，${rec.reason}"
            }
            AiTextBubble(text = recText, isStreaming = false)
        }
    }
}

// ===== 反问选项 =====

@OptIn(ExperimentalLayoutApi::class)
@Composable
private fun ClarificationMessage(question: String, options: List<String>, onSelected: (String) -> Unit) {
    Column {
        if (question.isNotBlank()) AiTextBubble(question, isStreaming = false)
        Spacer(Modifier.height(10.dp))
        FlowRow(
            horizontalArrangement = Arrangement.spacedBy(8.dp),
            verticalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            options.forEach { option ->
                Box(
                    modifier = Modifier
                        .clip(RoundedCornerShape(20.dp))
                        .background(
                            Brush.linearGradient(listOf(BrandIndigo, BrandViolet)),
                            RoundedCornerShape(20.dp),
                        )
                        .padding(1.5.dp)
                        .clip(RoundedCornerShape(18.5.dp))
                        .background(MaterialTheme.colorScheme.surface)
                        .clickable { onSelected(option) }
                        .padding(horizontal = 16.dp, vertical = 9.dp),
                ) {
                    Text(
                        option,
                        style = MaterialTheme.typography.bodyMedium,
                        color = BrandIndigo,
                        fontWeight = FontWeight.Medium,
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

    // 当前选择对应的 SKU。selectedProps 永远由"点击时跳转到真实 SKU"维护
    // （见下方 FilterChip onClick），所以这里总能完全命中；?: 仅作兜底。
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

            // ── 可滚动内容区（占用除底部按钮外所有空间）──
            // weight(1f) 配合 verticalScroll：内容超出时可滚动，按钮永远固定在底部不被裁
            Column(
                modifier = Modifier
                    .weight(1f, fill = false)
                    .fillMaxWidth()
                    .verticalScroll(rememberScrollState()),
            ) {

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
                // 顶部拖动条
                Box(
                    modifier = Modifier
                        .align(Alignment.TopCenter)
                        .padding(top = 12.dp)
                        .width(36.dp)
                        .height(4.dp)
                        .clip(RoundedCornerShape(2.dp))
                        .background(Color.White.copy(alpha = 0.6f))
                )
                // 右上角关闭按钮
                IconButton(
                    onClick = {
                        scope.launch {
                            sheetState.hide()
                            onDismiss()
                        }
                    },
                    modifier = Modifier
                        .align(Alignment.TopEnd)
                        .padding(8.dp)
                        .size(36.dp)
                        .clip(CircleShape)
                        .background(Color.Black.copy(alpha = 0.35f)),
                ) {
                    Icon(
                        Icons.Default.Close,
                        contentDescription = "关闭",
                        tint = Color.White,
                        modifier = Modifier.size(20.dp),
                    )
                }
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
                    // 只有一个可选值时不展示（固定规格，不需要用户选择）
                    if (values.size <= 1) return@forEach
                    Column(modifier = Modifier.fillMaxWidth()) {
                        Text(
                            key,
                            style = MaterialTheme.typography.bodyMedium,
                            fontWeight = FontWeight.Medium,
                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                        )
                        Spacer(Modifier.height(8.dp))
                        // FlowRow 自动换行，避免长文本 chip 溢出截断
                        FlowRow(
                            horizontalArrangement = Arrangement.spacedBy(8.dp),
                            verticalArrangement = Arrangement.spacedBy(8.dp),
                        ) {
                            values.forEach { v ->
                                val selected = selectedProps[key] == v
                                FilterChip(
                                    selected = selected,
                                    onClick = {
                                        val target = product.skus
                                            .filter { it.properties[key] == v }
                                            .maxByOrNull { sku ->
                                                selectedProps.count { (k, vv) -> sku.properties[k] == vv }
                                            }
                                        if (target != null) selectedProps = target.properties
                                    },
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
                    Spacer(Modifier.height(16.dp))
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
                    )
                    Spacer(Modifier.height(24.dp))
                }

            }
            }
            // ── 可滚动内容区到此结束 ──

            // ── 操作按钮（sticky bottom，永远可见）──
            val cartTransition = rememberInfiniteTransition(label = "cart_gradient")
            val cartShift by cartTransition.animateFloat(
                initialValue = -400f,
                targetValue = 400f,
                animationSpec = infiniteRepeatable(
                    animation = tween(2000, easing = LinearEasing),
                    repeatMode = RepeatMode.Restart,
                ),
                label = "cart_shift",
            )
            val cartGradient = Brush.linearGradient(
                colors = listOf(BrandIndigo, BrandViolet, BrandSky, BrandIndigo),
                start = Offset(cartShift, 0f),
                end = Offset(cartShift + 400f, 200f),
            )

            Row(
                modifier = Modifier
                    .fillMaxWidth()
                    .background(MaterialTheme.colorScheme.surface)
                    .padding(horizontal = 24.dp, vertical = 16.dp),
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
                    border = BorderStroke(
                        1.5.dp,
                        Brush.linearGradient(listOf(BrandIndigo, BrandViolet)),
                    ),
                ) {
                    Text(
                        "继续看看",
                        style = MaterialTheme.typography.titleSmall,
                        color = BrandIndigo,
                    )
                }
                Box(
                    modifier = Modifier
                        .weight(1.4f)
                        .height(54.dp)
                        .clip(RoundedCornerShape(27.dp))
                        .background(
                            if (matchedSku != null) cartGradient
                            else Brush.linearGradient(
                                listOf(
                                    MaterialTheme.colorScheme.outline.copy(alpha = 0.25f),
                                    MaterialTheme.colorScheme.outline.copy(alpha = 0.25f),
                                )
                            )
                        )
                        .clickable(enabled = matchedSku != null) {
                            matchedSku?.let { onAddToCart(product.productId, it.skuId) }
                        },
                    contentAlignment = Alignment.Center,
                ) {
                    Text(
                        "加入购物车",
                        style = MaterialTheme.typography.titleMedium,
                        fontWeight = FontWeight.Bold,
                        color = if (matchedSku != null) Color.White
                                else MaterialTheme.colorScheme.outline,
                    )
                }
            }
        }
    }
}

// ===== 会话抽屉(豆包式) =====

@Composable
private fun SessionDrawer(
    sessions: List<SessionSummary>,
    currentId: String,
    onNewSession: () -> Unit,
    onSwitch: (String) -> Unit,
) {
    ModalDrawerSheet {
        Column(
            modifier = Modifier
                .fillMaxSize()
                .padding(horizontal = 12.dp),
        ) {
            Spacer(Modifier.height(16.dp))
            Text(
                "对话",
                style = MaterialTheme.typography.titleMedium,
                fontWeight = FontWeight.Bold,
                modifier = Modifier.padding(start = 8.dp, bottom = 8.dp),
            )
            NavigationDrawerItem(
                label = { Text("新建对话") },
                icon = { Icon(Icons.Default.Add, contentDescription = null) },
                selected = false,
                onClick = onNewSession,
            )
            HorizontalDivider(modifier = Modifier.padding(vertical = 8.dp))

            if (sessions.isEmpty()) {
                Text(
                    "还没有历史对话",
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.outline,
                    modifier = Modifier.padding(8.dp),
                )
            } else {
                LazyColumn(modifier = Modifier.weight(1f)) {
                    items(sessions, key = { it.sessionId }) { s ->
                        NavigationDrawerItem(
                            label = {
                                Text(
                                    s.preview.ifBlank { "新对话" },
                                    maxLines = 1,
                                    overflow = TextOverflow.Ellipsis,
                                )
                            },
                            icon = { Icon(Icons.Default.ChatBubbleOutline, contentDescription = null) },
                            selected = s.sessionId == currentId,
                            onClick = { onSwitch(s.sessionId) },
                            modifier = Modifier.padding(NavigationDrawerItemDefaults.ItemPadding),
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
