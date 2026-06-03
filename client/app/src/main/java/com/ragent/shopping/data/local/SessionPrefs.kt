package com.ragent.shopping.data.local

import android.content.Context
import androidx.datastore.preferences.core.edit
import androidx.datastore.preferences.core.stringPreferencesKey
import androidx.datastore.preferences.preferencesDataStore
import kotlinx.coroutines.flow.first

// App 级单例 DataStore，存当前会话 ID。
private val Context.dataStore by preferencesDataStore(name = "ragent_prefs")

/**
 * 持久化当前会话 ID —— App 重启后复用同一会话，保住服务端上下文与购物车。
 * 在 MainActivity.onCreate 调一次 init() 注入 applicationContext。
 */
object SessionPrefs {
    private val KEY_SESSION_ID   = stringPreferencesKey("current_session_id")
    private val KEY_LAST_NAME    = stringPreferencesKey("last_receiver_name")
    private val KEY_LAST_PHONE   = stringPreferencesKey("last_receiver_phone")
    private val KEY_LAST_ADDRESS = stringPreferencesKey("last_receiver_address")

    private lateinit var appContext: Context

    fun init(context: Context) {
        appContext = context.applicationContext
    }

    suspend fun getSessionId(): String? =
        appContext.dataStore.data.first()[KEY_SESSION_ID]

    suspend fun setSessionId(id: String) {
        appContext.dataStore.edit { it[KEY_SESSION_ID] = id }
    }

    suspend fun clear() {
        appContext.dataStore.edit { it.remove(KEY_SESSION_ID) }
    }

    /** 读取上次填写的收货信息（跨会话全局存储） */
    suspend fun getLastReceiverInfo(): Triple<String, String, String> {
        val prefs = appContext.dataStore.data.first()
        return Triple(
            prefs[KEY_LAST_NAME]    ?: "",
            prefs[KEY_LAST_PHONE]   ?: "",
            prefs[KEY_LAST_ADDRESS] ?: "",
        )
    }

    /** 保存本次填写的收货信息，下次自动预填 */
    suspend fun saveReceiverInfo(name: String, phone: String, address: String) {
        appContext.dataStore.edit {
            it[KEY_LAST_NAME]    = name
            it[KEY_LAST_PHONE]   = phone
            it[KEY_LAST_ADDRESS] = address
        }
    }
}
