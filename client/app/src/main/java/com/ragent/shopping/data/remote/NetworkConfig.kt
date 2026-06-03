package com.ragent.shopping.data.remote

object NetworkConfig {
    // adb reverse tcp:8000 tcp:8000 后，真机和模拟器都用 localhost
    // 如需模拟器无 USB 直连：改回 http://10.0.2.2:8000
    const val BASE_URL = "http://localhost:8000"

    // 服务端返回相对路径如 /static/images/xxx.jpg，拼接成完整 URL
    fun imageUrl(relativePath: String): String {
        if (relativePath.isBlank()) return ""
        return if (relativePath.startsWith("http")) relativePath else "$BASE_URL$relativePath"
    }
}
