package com.ragent.shopping.ui.theme

import android.app.Activity
import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.darkColorScheme
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.runtime.SideEffect
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.toArgb
import androidx.compose.ui.platform.LocalView
import androidx.core.view.WindowCompat

private val LightColorScheme = lightColorScheme(
    primary             = BrandIndigo,
    onPrimary           = Color.White,
    primaryContainer    = Color(0xFFEEF0FF),
    onPrimaryContainer  = BrandIndigo,
    secondary           = BrandViolet,
    onSecondary         = Color.White,
    background          = BackgroundLight,
    onBackground        = OnSurfaceLight,
    surface             = SurfaceLight,
    onSurface           = OnSurfaceLight,
    surfaceVariant      = SurfaceVariantLight,
    onSurfaceVariant    = OnSurfaceVariantLight,
    outline             = OutlineLight,
    outlineVariant      = OutlineVariantLight,
    error               = PriceRed,
    onError             = Color.White,
)

private val DarkColorScheme = darkColorScheme(
    primary             = Color(0xFF8B9AFF),
    onPrimary           = Color(0xFF0E0F1A),
    primaryContainer    = Color(0xFF1E2140),
    onPrimaryContainer  = Color(0xFFBBC3FF),
    secondary           = Color(0xFFBB8AFF),
    onSecondary         = Color(0xFF0E0F1A),
    background          = BackgroundDark,
    onBackground        = OnSurfaceDark,
    surface             = SurfaceDark,
    onSurface           = OnSurfaceDark,
    surfaceVariant      = SurfaceVariantDark,
    onSurfaceVariant    = Color(0xFFB0B7C3),
    outline             = OutlineDark,
    outlineVariant      = OutlineVariantDark,
    error               = Color(0xFFFF6B6B),
    onError             = Color.White,
)

@Composable
fun RAGentTheme(
    darkTheme: Boolean = isSystemInDarkTheme(),
    content: @Composable () -> Unit,
) {
    val colorScheme = if (darkTheme) DarkColorScheme else LightColorScheme

    val view = LocalView.current
    if (!view.isInEditMode) {
        SideEffect {
            val window = (view.context as Activity).window
            window.statusBarColor = Color.Transparent.toArgb()
            WindowCompat.getInsetsController(window, view).isAppearanceLightStatusBars = !darkTheme
        }
    }

    MaterialTheme(
        colorScheme = colorScheme,
        typography  = Typography,
        content     = content,
    )
}
