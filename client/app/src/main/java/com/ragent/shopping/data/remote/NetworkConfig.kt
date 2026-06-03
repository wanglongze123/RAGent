package com.ragent.shopping.data.remote

object NetworkConfig {
    // 模拟器: 10.0.2.2   真机: Mac 局域网 IP
    const val BASE_URL = "http://10.229.176.117:8000"

    // 服务端返回相对路径如 /static/images/xxx.jpg，拼接成完整 URL
    fun imageUrl(relativePath: String): String {
        if (relativePath.isBlank()) return ""
        return if (relativePath.startsWith("http")) relativePath else "$BASE_URL$relativePath"
    }
}
