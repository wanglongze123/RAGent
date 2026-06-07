package com.ragent.shopping.data.local

import android.content.Context
import java.util.UUID

/**
 * 设备唯一标识 —— 首次启动生成一个随机 UUID 并持久化，之后永久复用（卸载重装才更换）。
 *
 * 服务端按此 ID（HTTP 头 X-Device-Id）隔离会话：每台设备只能看到自己的历史会话。
 *
 * 用同步的 SharedPreferences 而非异步 DataStore：OkHttp 拦截器需要同步读取。
 * 在 MainActivity.onCreate 调一次 init() 注入 applicationContext，之后 get() 随时可用。
 */
object DeviceId {
    private const val PREFS = "ragent_device"
    private const val KEY = "device_id"

    @Volatile
    private var cached: String = ""

    fun init(context: Context) {
        if (cached.isNotEmpty()) return
        val sp = context.applicationContext.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
        cached = sp.getString(KEY, null) ?: UUID.randomUUID().toString().also {
            sp.edit().putString(KEY, it).apply()
        }
    }

    /** 返回设备 ID；init() 之前调用返回空串（拦截器会带空头，服务端按无 device 处理）。 */
    fun get(): String = cached
}
