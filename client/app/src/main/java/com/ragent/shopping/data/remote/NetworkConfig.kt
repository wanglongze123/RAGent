package com.ragent.shopping.data.remote

import com.ragent.shopping.BuildConfig

object NetworkConfig {
    // debug: localhost（adb reverse），release: 云端服务器
    val BASE_URL: String = BuildConfig.BASE_URL

    fun imageUrl(relativePath: String): String {
        if (relativePath.isBlank()) return ""
        return if (relativePath.startsWith("http")) relativePath else "$BASE_URL$relativePath"
    }
}
