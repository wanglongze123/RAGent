package com.ragent.shopping.ui.screen

import androidx.compose.animation.animateColorAsState
import androidx.compose.animation.core.animateDpAsState
import androidx.compose.animation.core.animateFloatAsState
import androidx.compose.animation.core.tween
import androidx.compose.animation.core.LinearEasing
import androidx.compose.animation.core.rememberInfiniteTransition
import androidx.compose.animation.core.animateFloat
import androidx.compose.animation.core.infiniteRepeatable
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.interaction.MutableInteractionSource
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyRow
import androidx.compose.foundation.lazy.itemsIndexed
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
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
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.ragent.shopping.data.model.SavedAddress
import com.ragent.shopping.ui.theme.BrandIndigo
import com.ragent.shopping.ui.theme.BrandSky
import com.ragent.shopping.ui.theme.BrandViolet

private val _PHONE_RE = Regex("^1[3-9]\\d{9}$")

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun OrderFormBottomSheet(
    savedAddresses: List<SavedAddress>,
    onSubmit: (name: String, phone: String, address: String) -> Unit,
    onDismiss: () -> Unit,
) {
    val sheetState = rememberModalBottomSheetState(skipPartiallyExpanded = true)

    var name    by remember { mutableStateOf("") }
    var phone   by remember { mutableStateOf("") }
    var address by remember { mutableStateOf("") }

    var nameTouched    by remember { mutableStateOf(false) }
    var phoneTouched   by remember { mutableStateOf(false) }
    var addressTouched by remember { mutableStateOf(false) }

    // 选中的历史地址 index（-1 = 无）
    var selectedAddrIdx by remember { mutableIntStateOf(-1) }

    val nameError    = nameTouched    && (name.trim().length < 2 || name.trim().length > 20)
    val phoneError   = phoneTouched   && !_PHONE_RE.matches(phone.trim())
    val addressError = addressTouched && address.trim().length < 5

    val canSubmit = name.trim().length in 2..20
            && _PHONE_RE.matches(phone.trim())
            && address.trim().length >= 5

    // 流光渐变动画（与 ProductDetailSheet 一致）
    val shimmerTransition = rememberInfiniteTransition(label = "shimmer")
    val shimmerOffset by shimmerTransition.animateFloat(
        initialValue = 0f,
        targetValue = 1000f,
        animationSpec = infiniteRepeatable(tween(2000, easing = LinearEasing)),
        label = "shimmerOffset",
    )
    val shimmerBrush = Brush.linearGradient(
        colors = listOf(BrandIndigo, BrandViolet, BrandSky, BrandIndigo),
        start = Offset(shimmerOffset - 500f, 0f),
        end   = Offset(shimmerOffset, 0f),
    )

    val submitAlpha by animateFloatAsState(
        targetValue = if (canSubmit) 1f else 0.45f,
        animationSpec = tween(200),
        label = "submitAlpha",
    )

    ModalBottomSheet(
        onDismissRequest = onDismiss,
        sheetState = sheetState,
        containerColor = MaterialTheme.colorScheme.surface,
        dragHandle = null,
        shape = RoundedCornerShape(topStart = 24.dp, topEnd = 24.dp),
    ) {
        Column(modifier = Modifier.fillMaxWidth()) {

            // ── 可滚动内容区 ──────────────────────────────────────
            Column(
                modifier = Modifier
                    .weight(1f, fill = false)
                    .fillMaxWidth()
                    .verticalScroll(rememberScrollState()),
            ) {
                // 手动拖动条
                Box(
                    modifier = Modifier
                        .fillMaxWidth()
                        .padding(top = 12.dp),
                    contentAlignment = Alignment.Center,
                ) {
                    Box(
                        modifier = Modifier
                            .size(width = 40.dp, height = 4.dp)
                            .clip(RoundedCornerShape(2.dp))
                            .background(MaterialTheme.colorScheme.outlineVariant),
                    )
                }

                Spacer(Modifier.height(20.dp))

                // 标题：流光渐变
                Text(
                    text = "填写收货信息",
                    style = MaterialTheme.typography.titleLarge.copy(
                        fontWeight = FontWeight.Bold,
                        fontSize = 20.sp,
                        brush = shimmerBrush,
                    ),
                    modifier = Modifier.padding(horizontal = 24.dp),
                )

                Spacer(Modifier.height(4.dp))

                Text(
                    text = "请填写准确信息，确保顺利送达",
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                    modifier = Modifier.padding(horizontal = 24.dp),
                )

                // ── 历史地址（若有）────────────────────────────────
                if (savedAddresses.isNotEmpty()) {
                    Spacer(Modifier.height(16.dp))

                    Text(
                        text = "历史地址",
                        style = MaterialTheme.typography.labelMedium,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                        modifier = Modifier.padding(horizontal = 24.dp),
                    )
                    Spacer(Modifier.height(8.dp))

                    LazyRow(
                        contentPadding = PaddingValues(horizontal = 24.dp),
                        horizontalArrangement = Arrangement.spacedBy(10.dp),
                    ) {
                        itemsIndexed(savedAddresses) { idx, addr ->
                            val isSelected = selectedAddrIdx == idx
                            val borderWidth by animateDpAsState(
                                targetValue = if (isSelected) 2.dp else 1.dp,
                                animationSpec = tween(150),
                                label = "borderWidth_$idx",
                            )
                            val borderColor by animateColorAsState(
                                targetValue = if (isSelected) BrandIndigo else MaterialTheme.colorScheme.outlineVariant,
                                animationSpec = tween(150),
                                label = "borderColor_$idx",
                            )

                            Column(
                                modifier = Modifier
                                    .width(200.dp)
                                    .clip(RoundedCornerShape(12.dp))
                                    .border(borderWidth, borderColor, RoundedCornerShape(12.dp))
                                    .background(
                                        if (isSelected)
                                            BrandIndigo.copy(alpha = 0.07f)
                                        else
                                            MaterialTheme.colorScheme.surfaceVariant
                                    )
                                    .clickable(
                                        interactionSource = remember { MutableInteractionSource() },
                                        indication = null,
                                    ) {
                                        selectedAddrIdx = idx
                                        name    = addr.name
                                        phone   = addr.phone
                                        address = addr.address
                                        nameTouched    = false
                                        phoneTouched   = false
                                        addressTouched = false
                                    }
                                    .padding(12.dp),
                            ) {
                                val maskedPhone = if (addr.phone.length == 11)
                                    "${addr.phone.take(3)}****${addr.phone.takeLast(4)}"
                                else addr.phone

                                Text(
                                    text = addr.name,
                                    style = MaterialTheme.typography.bodyMedium.copy(fontWeight = FontWeight.SemiBold),
                                    color = MaterialTheme.colorScheme.onSurface,
                                )
                                Spacer(Modifier.height(2.dp))
                                Text(
                                    text = maskedPhone,
                                    style = MaterialTheme.typography.bodySmall,
                                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                                )
                                Spacer(Modifier.height(2.dp))
                                Text(
                                    text = addr.address,
                                    style = MaterialTheme.typography.bodySmall,
                                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                                    maxLines = 1,
                                    overflow = androidx.compose.ui.text.style.TextOverflow.Ellipsis,
                                )
                            }
                        }
                    }
                }

                Spacer(Modifier.height(20.dp))

                // ── 表单字段 ──────────────────────────────────────
                Column(
                    modifier = Modifier.padding(horizontal = 24.dp),
                    verticalArrangement = Arrangement.spacedBy(14.dp),
                ) {
                    FormField(
                        value = name,
                        onValueChange = { name = it; nameTouched = true; selectedAddrIdx = -1 },
                        label = "收货人姓名",
                        placeholder = "请输入姓名",
                        icon = Icons.Outlined.Person,
                        isError = nameError,
                        errorText = "姓名长度需在 2~20 字之间",
                        keyboardOptions = KeyboardOptions(
                            keyboardType = KeyboardType.Text,
                            imeAction = ImeAction.Next,
                        ),
                    )

                    FormField(
                        value = phone,
                        onValueChange = {
                            if (it.length <= 11) {
                                phone = it
                                phoneTouched = it.length == 11
                                selectedAddrIdx = -1
                            }
                        },
                        label = "手机号码",
                        placeholder = "请输入 11 位手机号",
                        icon = Icons.Outlined.Phone,
                        isError = phoneError,
                        errorText = "请输入有效的中国大陆手机号",
                        keyboardOptions = KeyboardOptions(
                            keyboardType = KeyboardType.Phone,
                            imeAction = ImeAction.Next,
                        ),
                    )

                    FormField(
                        value = address,
                        onValueChange = { address = it; addressTouched = true; selectedAddrIdx = -1 },
                        label = "收货地址",
                        placeholder = "省市区 + 街道门牌号",
                        icon = Icons.Outlined.LocationOn,
                        isError = addressError,
                        errorText = "地址过于简短，请填写完整地址",
                        keyboardOptions = KeyboardOptions(
                            keyboardType = KeyboardType.Text,
                            imeAction = ImeAction.Done,
                        ),
                        minLines = 2,
                    )
                }

                Spacer(Modifier.height(24.dp))
            }

            // ── Sticky 底部按钮 ───────────────────────────────────
            HorizontalDivider(color = MaterialTheme.colorScheme.outlineVariant)
            Row(
                modifier = Modifier
                    .fillMaxWidth()
                    .navigationBarsPadding()
                    .padding(horizontal = 24.dp, vertical = 16.dp),
                horizontalArrangement = Arrangement.spacedBy(12.dp),
            ) {
                OutlinedButton(
                    onClick = onDismiss,
                    modifier = Modifier
                        .weight(1f)
                        .height(52.dp),
                    shape = RoundedCornerShape(26.dp),
                ) {
                    Text("取消下单", fontWeight = FontWeight.Medium)
                }

                // 流光渐变提交按钮
                Box(
                    modifier = Modifier
                        .weight(2f)
                        .height(52.dp)
                        .alpha(submitAlpha)
                        .clip(RoundedCornerShape(26.dp))
                        .background(shimmerBrush)
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
                    )
                }
            }
        }
    }
}

@Composable
private fun FormField(
    value: String,
    onValueChange: (String) -> Unit,
    label: String,
    placeholder: String,
    icon: ImageVector,
    isError: Boolean,
    errorText: String,
    keyboardOptions: KeyboardOptions,
    minLines: Int = 1,
) {
    OutlinedTextField(
        value = value,
        onValueChange = onValueChange,
        label = { Text(label) },
        placeholder = { Text(placeholder, color = MaterialTheme.colorScheme.onSurfaceVariant.copy(alpha = 0.5f)) },
        leadingIcon = {
            Icon(
                imageVector = icon,
                contentDescription = null,
                tint = if (isError) MaterialTheme.colorScheme.error
                       else BrandIndigo.copy(alpha = 0.8f),
            )
        },
        isError = isError,
        supportingText = if (isError) {
            { Text(errorText, color = MaterialTheme.colorScheme.error) }
        } else null,
        keyboardOptions = keyboardOptions,
        minLines = minLines,
        shape = RoundedCornerShape(14.dp),
        colors = OutlinedTextFieldDefaults.colors(
            focusedBorderColor = BrandIndigo,
            focusedLabelColor  = BrandIndigo,
            cursorColor        = BrandIndigo,
        ),
        modifier = Modifier.fillMaxWidth(),
        singleLine = minLines == 1,
    )
}
