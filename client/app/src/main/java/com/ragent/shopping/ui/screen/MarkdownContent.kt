package com.ragent.shopping.ui.screen

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.*
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.AnnotatedString
import androidx.compose.ui.text.SpanStyle
import androidx.compose.ui.text.buildAnnotatedString
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontStyle
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.withStyle
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.ragent.shopping.ui.theme.BrandIndigo
import com.ragent.shopping.ui.theme.BrandViolet

/**
 * Markdown 渲染组件（无第三方依赖，纯 Compose 实现）。
 * 支持：## 标题、- 无序列表、1. 有序列表、> 引用、--- 分割线、**加粗**、*斜体*、`代码`。
 * 流式兼容：不完整 Markdown 语法降级为普通文字。
 */
@Composable
fun MarkdownContent(
    text: String,
    modifier: Modifier = Modifier,
    baseStyle: androidx.compose.ui.text.TextStyle = MaterialTheme.typography.bodyMedium,
    baseColor: Color = Color(0xFF1A1A1A),
) {
    // 把文本分成逻辑"块"（连续的列表行合并为一组）
    val blocks = parseBlocks(text)

    Column(modifier = modifier, verticalArrangement = Arrangement.spacedBy(0.dp)) {
        blocks.forEachIndexed { i, block ->
            when (block) {
                is Block.H1 -> {
                    if (i > 0) Spacer(Modifier.height(8.dp))
                    Text(
                        text = parseInline(block.content),
                        style = baseStyle.copy(
                            fontSize = 17.sp,
                            fontWeight = FontWeight.Bold,
                            lineHeight = 24.sp,
                        ),
                        color = baseColor,
                    )
                    Spacer(Modifier.height(2.dp))
                }
                is Block.H2 -> {
                    if (i > 0) Spacer(Modifier.height(6.dp))
                    Text(
                        text = parseInline(block.content),
                        style = baseStyle.copy(
                            fontSize = 15.sp,
                            fontWeight = FontWeight.Bold,
                            lineHeight = 22.sp,
                        ),
                        color = baseColor,
                    )
                    Spacer(Modifier.height(2.dp))
                }
                is Block.H3 -> {
                    if (i > 0) Spacer(Modifier.height(4.dp))
                    Text(
                        text = parseInline(block.content),
                        style = baseStyle.copy(fontWeight = FontWeight.SemiBold),
                        color = BrandIndigo,
                    )
                }
                is Block.Bullet -> {
                    Row(
                        modifier = Modifier.fillMaxWidth().padding(vertical = 1.dp),
                        horizontalArrangement = Arrangement.spacedBy(6.dp),
                    ) {
                        Text(
                            "•",
                            style = baseStyle.copy(fontWeight = FontWeight.Bold),
                            color = BrandIndigo,
                        )
                        Text(
                            text = parseInline(block.content),
                            style = baseStyle.copy(lineHeight = 20.sp),
                            color = baseColor,
                            modifier = Modifier.weight(1f),
                        )
                    }
                }
                is Block.Numbered -> {
                    Row(
                        modifier = Modifier.fillMaxWidth().padding(vertical = 1.dp),
                        horizontalArrangement = Arrangement.spacedBy(6.dp),
                    ) {
                        Text(
                            "${block.num}.",
                            style = baseStyle.copy(fontWeight = FontWeight.SemiBold),
                            color = BrandIndigo,
                            modifier = Modifier.widthIn(min = 20.dp),
                        )
                        Text(
                            text = parseInline(block.content),
                            style = baseStyle.copy(lineHeight = 20.sp),
                            color = baseColor,
                            modifier = Modifier.weight(1f),
                        )
                    }
                }
                is Block.Blockquote -> {
                    Row(
                        modifier = Modifier.fillMaxWidth(),
                        horizontalArrangement = Arrangement.spacedBy(8.dp),
                    ) {
                        Box(
                            modifier = Modifier
                                .width(3.dp)
                                .height(18.dp)
                                .background(BrandViolet.copy(alpha = 0.5f)),
                        )
                        Text(
                            text = parseInline(block.content),
                            style = baseStyle.copy(fontStyle = FontStyle.Italic),
                            color = baseColor.copy(alpha = 0.6f),
                        )
                    }
                }
                is Block.Rule -> {
                    Spacer(Modifier.height(4.dp))
                    HorizontalDivider(
                        color = MaterialTheme.colorScheme.outlineVariant,
                        thickness = 0.5.dp,
                    )
                    Spacer(Modifier.height(4.dp))
                }
                is Block.Blank -> Spacer(Modifier.height(4.dp))
                is Block.Paragraph -> {
                    Text(
                        text = parseInline(block.content),
                        style = baseStyle.copy(lineHeight = 20.sp),
                        color = baseColor,
                    )
                }
            }
        }
    }
}

// ── 块解析 ─────────────────────────────────────────────────────

private sealed class Block {
    data class H1(val content: String)         : Block()
    data class H2(val content: String)         : Block()
    data class H3(val content: String)         : Block()
    data class Bullet(val content: String)     : Block()
    data class Numbered(val num: Int, val content: String) : Block()
    data class Blockquote(val content: String) : Block()
    object Rule                                : Block()
    object Blank                               : Block()
    data class Paragraph(val content: String)  : Block()
}

private val NUMBERED_RE = Regex("""^(\d+)\.\s+(.*)""")

private fun parseBlocks(text: String): List<Block> {
    val blocks = mutableListOf<Block>()
    for (line in text.split("\n")) {
        val block = when {
            line.startsWith("# ")   -> Block.H1(line.removePrefix("# ").trim())
            line.startsWith("## ")  -> Block.H2(line.removePrefix("## ").trim())
            line.startsWith("### ") -> Block.H3(line.removePrefix("### ").trim())
            line.startsWith("- ")   -> Block.Bullet(line.removePrefix("- ").trim())
            line.startsWith("* ")   -> Block.Bullet(line.removePrefix("* ").trim())
            line.startsWith("> ")   -> Block.Blockquote(line.removePrefix("> ").trim())
            line.trim() == "---" || line.trim() == "***" -> Block.Rule
            line.isBlank()          -> Block.Blank
            else -> {
                val m = NUMBERED_RE.matchEntire(line.trim())
                if (m != null) Block.Numbered(m.groupValues[1].toInt(), m.groupValues[2])
                else Block.Paragraph(line)
            }
        }
        // 合并连续空白块为一个
        if (block is Block.Blank && blocks.lastOrNull() is Block.Blank) continue
        blocks += block
    }
    return blocks
}

// ── 行内解析（**bold** *italic* `code`）─────────────────────

fun parseInline(text: String): AnnotatedString = buildAnnotatedString {
    var i = 0
    while (i < text.length) {
        when {
            // **bold**
            text.startsWith("**", i) -> {
                val end = text.indexOf("**", i + 2)
                if (end != -1) {
                    withStyle(SpanStyle(fontWeight = FontWeight.Bold)) {
                        append(text.substring(i + 2, end))
                    }
                    i = end + 2
                } else { append(text[i]); i++ }
            }
            // *italic*
            text.startsWith("*", i) && !text.startsWith("**", i) -> {
                val end = text.indexOf("*", i + 1)
                if (end != -1 && !text.startsWith("**", end)) {
                    withStyle(SpanStyle(fontStyle = FontStyle.Italic)) {
                        append(text.substring(i + 1, end))
                    }
                    i = end + 1
                } else { append(text[i]); i++ }
            }
            // `code`
            text.startsWith("`", i) -> {
                val end = text.indexOf("`", i + 1)
                if (end != -1) {
                    withStyle(SpanStyle(
                        fontFamily = FontFamily.Monospace,
                        background = Color(0x14000000),
                        fontSize = 12.sp,
                    )) {
                        append(text.substring(i + 1, end))
                    }
                    i = end + 1
                } else { append(text[i]); i++ }
            }
            else -> { append(text[i]); i++ }
        }
    }
}
