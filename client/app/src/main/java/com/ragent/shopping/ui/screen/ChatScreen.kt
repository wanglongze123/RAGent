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
import androidx.compose.animation.core.RepeatMode
import androidx.compose.animation.core.animateFloat
import androidx.compose.animation.core.infiniteRepeatable
import androidx.compose.animation.core.rememberInfiniteTransition
import androidx.compose.animation.core.tween
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
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.FilterChip
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Scaffold
import androidx.compose.material3.SnackbarHost
import androidx.compose.material3.SnackbarHostState
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
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
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
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

    // 自动滚动到最新消息
    LaunchedEffect(uiState.messages.size) {
        if (uiState.messages.isNotEmpty()) {
            listState.animateScrollToItem(uiState.messages.size - 1)
        }
    }

    // 购物车操作 toast
    LaunchedEffect(uiState.toastMessage) {
        if (uiState.toastMessage.isNotEmpty()) {
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
                        onProductClick = onNavigateToProduct,
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
        is ChatMessage.AiProductList -> ProductListMessage(message.products, onProductClick)
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
                Text(product.title, style = MaterialTheme.typography.bodyMedium, fontWeight = FontWeight.Medium, maxLines = 2, overflow = TextOverflow.Ellipsis)
                Spacer(Modifier.height(4.dp))
                Text("¥%.2f".format(product.displayPrice), color = MaterialTheme.colorScheme.error, fontWeight = FontWeight.Bold)
                product.reason?.let {
                    Spacer(Modifier.height(4.dp))
                    Text(it, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.outline, maxLines = 2, overflow = TextOverflow.Ellipsis)
                }
                product.similarityScore?.let {
                    Text("匹配度 ${"%.0f".format(it * 100)}%", style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.primary)
                }
            }
        }
    }
}

@Composable
private fun ProductListMessage(products: List<Product>, onProductClick: (String) -> Unit) {
    Column {
        Text("为您找到 ${products.size} 款商品", style = MaterialTheme.typography.labelMedium, color = MaterialTheme.colorScheme.outline)
        Spacer(Modifier.height(6.dp))
        LazyRow(horizontalArrangement = Arrangement.spacedBy(10.dp)) {
            items(products) { product ->
                ProductCardCompact(product, onProductClick)
            }
        }
    }
}

@Composable
private fun ProductCardCompact(product: Product, onClick: (String) -> Unit) {
    Card(
        modifier = Modifier
            .width(140.dp)
            .clickable { onClick(product.productId) },
        elevation = CardDefaults.cardElevation(defaultElevation = 2.dp),
    ) {
        Column {
            AsyncImage(
                model = NetworkConfig.imageUrl(product.imageUrl),
                contentDescription = product.title,
                modifier = Modifier
                    .fillMaxWidth()
                    .height(120.dp),
                contentScale = ContentScale.Crop,
            )
            Column(modifier = Modifier.padding(8.dp)) {
                Text(product.title, style = MaterialTheme.typography.bodySmall, maxLines = 2, overflow = TextOverflow.Ellipsis)
                Spacer(Modifier.height(2.dp))
                Text("¥%.2f".format(product.displayPrice), color = MaterialTheme.colorScheme.error, style = MaterialTheme.typography.labelMedium, fontWeight = FontWeight.Bold)
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
        Spacer(Modifier.height(6.dp))
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp), modifier = Modifier.fillMaxWidth()) {
            options.forEach { option ->
                FilterChip(
                    selected = false,
                    onClick = { onSelected(option) },
                    label = { Text(option, style = MaterialTheme.typography.bodySmall) },
                )
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
