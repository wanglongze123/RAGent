package com.ragent.shopping.ui.screen

import androidx.compose.animation.animateColorAsState
import androidx.compose.animation.core.LinearEasing
import androidx.compose.animation.core.animateDpAsState
import androidx.compose.animation.core.animateFloat
import androidx.compose.animation.core.animateFloatAsState
import androidx.compose.animation.core.infiniteRepeatable
import androidx.compose.animation.core.rememberInfiniteTransition
import androidx.compose.animation.core.tween
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.interaction.MutableInteractionSource
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyRow
import androidx.compose.foundation.lazy.itemsIndexed
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.BasicTextField
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.outlined.CheckCircle
import androidx.compose.material.icons.outlined.Close
import androidx.compose.material.icons.outlined.LocationOn
import androidx.compose.material.icons.outlined.Person
import androidx.compose.material.icons.outlined.Phone
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.alpha
import androidx.compose.ui.draw.clip
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.SolidColor
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.ragent.shopping.data.model.SavedAddress
import com.ragent.shopping.ui.theme.BrandIndigo
import com.ragent.shopping.ui.theme.BrandSky
import com.ragent.shopping.ui.theme.BrandViolet

private val PHONE_RE = Regex("^1[3-9]\\d{9}\$")

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun OrderFormBottomSheet(
    savedAddresses: List<SavedAddress>,
    initialName: String = "",
    initialPhone: String = "",
    initialAddress: String = "",
    onSubmit: (name: String, phone: String, address: String) -> Unit,
    onDismiss: () -> Unit,
) {
    val sheetState = rememberModalBottomSheetState(skipPartiallyExpanded = true)

    var name    by remember { mutableStateOf(initialName) }
    var phone   by remember { mutableStateOf(initialPhone) }
    var address by remember { mutableStateOf(initialAddress) }

    var nameTouched    by remember { mutableStateOf(initialName.isNotEmpty()) }
    var phoneTouched   by remember { mutableStateOf(initialPhone.isNotEmpty()) }
    var addressTouched by remember { mutableStateOf(initialAddress.isNotEmpty()) }
    var selectedAddrIdx by remember { mutableIntStateOf(-1) }

    val nameError    = nameTouched    && (name.trim().length < 2 || name.trim().length > 20)
    val phoneError   = phoneTouched   && !PHONE_RE.matches(phone.trim())
    val addressError = addressTouched && address.trim().length < 5
    val canSubmit    = name.trim().length in 2..20
            && PHONE_RE.matches(phone.trim())
            && address.trim().length >= 5

    // 流光渐变（header + 提交按钮共用）
    val shimmer = rememberInfiniteTransition(label = "shimmer")
    val shimmerX by shimmer.animateFloat(
        initialValue = 0f, targetValue = 800f,
        animationSpec = infiniteRepeatable(tween(2000, easing = LinearEasing)),
        label = "sx",
    )
    val gradientBrush = Brush.linearGradient(
        colors = listOf(BrandIndigo, BrandViolet, BrandSky, BrandIndigo),
        start  = Offset(shimmerX - 400f, 0f),
        end    = Offset(shimmerX + 400f, 0f),
    )
    val submitAlpha by animateFloatAsState(if (canSubmit) 1f else 0.4f, tween(200), label = "sa")

    ModalBottomSheet(
        onDismissRequest = onDismiss,
        sheetState = sheetState,
        containerColor = MaterialTheme.colorScheme.surface,
        dragHandle = null,
        shape = RoundedCornerShape(topStart = 20.dp, topEnd = 20.dp),
    ) {
        Column(Modifier.fillMaxWidth()) {

            // ── 可滚动内容区 ──────────────────────────────────
            Column(
                modifier = Modifier
                    .weight(1f, fill = false)
                    .fillMaxWidth()
                    .verticalScroll(rememberScrollState()),
            ) {
                // ── Header ───────────────────────────────────
                Box(
                    modifier = Modifier
                        .fillMaxWidth()
                        .background(gradientBrush)
                        .padding(horizontal = 20.dp, vertical = 18.dp),
                ) {
                    Text(
                        text = "填写收货信息",
                        style = MaterialTheme.typography.titleMedium.copy(
                            fontWeight = FontWeight.Bold,
                            color = Color.White,
                            fontSize = 17.sp,
                        ),
                        modifier = Modifier.align(Alignment.CenterStart),
                    )
                    IconButton(
                        onClick = onDismiss,
                        modifier = Modifier
                            .align(Alignment.CenterEnd)
                            .size(32.dp),
                    ) {
                        Icon(
                            Icons.Outlined.Close,
                            contentDescription = "关闭",
                            tint = Color.White.copy(alpha = 0.9f),
                            modifier = Modifier.size(20.dp),
                        )
                    }
                }

                Spacer(Modifier.height(16.dp))

                // ── 历史地址（若有）──────────────────────────
                if (savedAddresses.isNotEmpty()) {
                    Text(
                        text = "历史地址",
                        style = MaterialTheme.typography.labelMedium,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                        modifier = Modifier.padding(horizontal = 20.dp),
                    )
                    Spacer(Modifier.height(8.dp))
                    LazyRow(
                        contentPadding = PaddingValues(horizontal = 20.dp),
                        horizontalArrangement = Arrangement.spacedBy(10.dp),
                    ) {
                        itemsIndexed(savedAddresses) { idx, addr ->
                            val selected = selectedAddrIdx == idx
                            val bw by animateDpAsState(if (selected) 2.dp else 1.dp, tween(150), label = "bw$idx")
                            val bc by animateColorAsState(
                                if (selected) BrandIndigo else MaterialTheme.colorScheme.outlineVariant,
                                tween(150), label = "bc$idx",
                            )
                            val masked = if (addr.phone.length == 11)
                                "${addr.phone.take(3)}****${addr.phone.takeLast(4)}" else addr.phone

                            Row(
                                modifier = Modifier
                                    .widthIn(min = 160.dp, max = 220.dp)
                                    .clip(RoundedCornerShape(10.dp))
                                    .border(bw, bc, RoundedCornerShape(10.dp))
                                    .background(
                                        if (selected) BrandIndigo.copy(alpha = 0.07f)
                                        else MaterialTheme.colorScheme.surfaceVariant
                                    )
                                    .clickable(
                                        interactionSource = remember { MutableInteractionSource() },
                                        indication = null,
                                    ) {
                                        selectedAddrIdx = idx
                                        name    = addr.name
                                        phone   = addr.phone
                                        address = addr.address
                                        nameTouched = false; phoneTouched = false; addressTouched = false
                                    }
                                    .padding(horizontal = 12.dp, vertical = 10.dp),
                                verticalAlignment = Alignment.CenterVertically,
                                horizontalArrangement = Arrangement.spacedBy(8.dp),
                            ) {
                                if (selected) {
                                    Icon(Icons.Outlined.CheckCircle, null,
                                        tint = BrandIndigo, modifier = Modifier.size(16.dp))
                                }
                                Column(Modifier.weight(1f)) {
                                    Text(
                                        text = "${addr.name}  $masked",
                                        style = MaterialTheme.typography.bodySmall.copy(fontWeight = FontWeight.SemiBold),
                                        color = MaterialTheme.colorScheme.onSurface,
                                        maxLines = 1,
                                        overflow = TextOverflow.Ellipsis,
                                    )
                                    Spacer(Modifier.height(2.dp))
                                    Text(
                                        text = addr.address,
                                        style = MaterialTheme.typography.labelSmall,
                                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                                        maxLines = 1,
                                        overflow = TextOverflow.Ellipsis,
                                    )
                                }
                            }
                        }
                    }
                    Spacer(Modifier.height(16.dp))
                }

                // ── 表单卡片 ──────────────────────────────────
                Card(
                    modifier = Modifier
                        .fillMaxWidth()
                        .padding(horizontal = 16.dp),
                    shape = RoundedCornerShape(14.dp),
                    elevation = CardDefaults.cardElevation(defaultElevation = 2.dp),
                    colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surface),
                ) {
                    Column {
                        FormRow(
                            icon      = Icons.Outlined.Person,
                            label     = "收货人",
                            value     = name,
                            onChange  = { name = it; nameTouched = true; selectedAddrIdx = -1 },
                            hint      = "请输入姓名",
                            isError   = nameError,
                            errorText = "2~20 个字符",
                            keyboard  = KeyboardOptions(keyboardType = KeyboardType.Text, imeAction = ImeAction.Next),
                        )
                        RowDivider()
                        FormRow(
                            icon      = Icons.Outlined.Phone,
                            label     = "手机号",
                            value     = phone,
                            onChange  = {
                                if (it.length <= 11) {
                                    phone = it; selectedAddrIdx = -1
                                    if (it.length == 11) phoneTouched = true
                                }
                            },
                            hint      = "11 位手机号",
                            isError   = phoneError,
                            errorText = "请输入有效的手机号",
                            keyboard  = KeyboardOptions(keyboardType = KeyboardType.Phone, imeAction = ImeAction.Next),
                        )
                        RowDivider()
                        FormRow(
                            icon      = Icons.Outlined.LocationOn,
                            label     = "收货地址",
                            value     = address,
                            onChange  = { address = it; addressTouched = true; selectedAddrIdx = -1 },
                            hint      = "省 / 市 / 区 / 街道门牌号",
                            isError   = addressError,
                            errorText = "地址太短，请填写完整地址",
                            keyboard  = KeyboardOptions(keyboardType = KeyboardType.Text, imeAction = ImeAction.Done),
                            singleLine = false,
                            minLines   = 2,
                        )
                    }
                }

                Spacer(Modifier.height(24.dp))
            }

            // ── Sticky 提交按钮 ──────────────────────────────
            HorizontalDivider(color = MaterialTheme.colorScheme.outlineVariant.copy(alpha = 0.5f))
            Box(
                modifier = Modifier
                    .fillMaxWidth()
                    .navigationBarsPadding()
                    .padding(horizontal = 16.dp, vertical = 14.dp)
                    .height(52.dp)
                    .alpha(submitAlpha)
                    .clip(RoundedCornerShape(26.dp))
                    .background(gradientBrush)
                    .clickable(enabled = canSubmit) {
                        onSubmit(name.trim(), phone.trim(), address.trim())
                    },
                contentAlignment = Alignment.Center,
            ) {
                Text(
                    text = "提交订单",
                    color = Color.White,
                    fontWeight = FontWeight.Bold,
                    fontSize = 16.sp,
                    letterSpacing = 1.sp,
                )
            }
        }
    }
}

// ── 单行表单行 ────────────────────────────────────────────────

@Composable
private fun FormRow(
    icon: ImageVector,
    label: String,
    value: String,
    onChange: (String) -> Unit,
    hint: String,
    isError: Boolean,
    errorText: String,
    keyboard: KeyboardOptions,
    singleLine: Boolean = true,
    minLines: Int = 1,
) {
    val labelColor = if (isError) MaterialTheme.colorScheme.error else MaterialTheme.colorScheme.onSurfaceVariant
    val iconTint   = if (isError) MaterialTheme.colorScheme.error else BrandIndigo.copy(alpha = 0.75f)

    Column(modifier = Modifier.fillMaxWidth()) {
        Row(
            modifier = Modifier
                .fillMaxWidth()
                .padding(horizontal = 16.dp, vertical = if (singleLine) 0.dp else 4.dp),
            verticalAlignment = if (singleLine) Alignment.CenterVertically else Alignment.Top,
        ) {
            // 图标
            Icon(
                icon, null,
                tint = iconTint,
                modifier = Modifier
                    .padding(top = if (singleLine) 0.dp else 14.dp)
                    .size(20.dp),
            )
            Spacer(Modifier.width(10.dp))
            // 标签
            Text(
                text = label,
                style = MaterialTheme.typography.bodyMedium.copy(fontWeight = FontWeight.Medium),
                color = labelColor,
                modifier = Modifier
                    .width(56.dp)
                    .padding(top = if (singleLine) 0.dp else 14.dp),
            )
            Spacer(Modifier.width(8.dp))
            // 输入框：无边框，透明背景，只保留光标
            BasicTextField(
                value = value,
                onValueChange = onChange,
                textStyle = TextStyle(
                    fontSize = 15.sp,
                    color = MaterialTheme.colorScheme.onSurface,
                    fontWeight = FontWeight.Normal,
                ),
                cursorBrush = SolidColor(BrandIndigo),
                keyboardOptions = keyboard,
                singleLine = singleLine,
                minLines = minLines,
                decorationBox = { inner ->
                    Box(
                        modifier = Modifier
                            .fillMaxWidth()
                            .padding(vertical = 14.dp),
                    ) {
                        if (value.isEmpty()) {
                            Text(
                                hint,
                                style = TextStyle(
                                    fontSize = 15.sp,
                                    color = MaterialTheme.colorScheme.onSurface.copy(alpha = 0.35f),
                                ),
                            )
                        }
                        inner()
                    }
                },
                modifier = Modifier.fillMaxWidth(),
            )
        }
        // 错误提示
        if (isError) {
            Text(
                text = "  $errorText",
                style = MaterialTheme.typography.labelSmall,
                color = MaterialTheme.colorScheme.error,
                modifier = Modifier.padding(start = 54.dp, bottom = 6.dp),
            )
        }
    }
}

@Composable
private fun RowDivider() {
    HorizontalDivider(
        modifier = Modifier.padding(start = 48.dp),
        color = MaterialTheme.colorScheme.outlineVariant.copy(alpha = 0.6f),
        thickness = 0.5.dp,
    )
}
