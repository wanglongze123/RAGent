package com.ragent.shopping.util

import android.content.Context
import android.speech.tts.TextToSpeech
import java.util.Locale

/**
 * 封装 Android TextToSpeech，处理异步初始化时序。
 * 在 onInit 回调前调用 speak() 时，文本暂存 pending，初始化完成后立即播放。
 */
class TtsManager(context: Context) {

    private var tts: TextToSpeech? = null
    private var ready = false
    private var pending: String? = null

    init {
        tts = TextToSpeech(context.applicationContext) { status ->
            if (status == TextToSpeech.SUCCESS) {
                val result = tts?.setLanguage(Locale.SIMPLIFIED_CHINESE)
                ready = result != TextToSpeech.LANG_MISSING_DATA &&
                        result != TextToSpeech.LANG_NOT_SUPPORTED
                if (ready) pending?.let { speak(it) }
                pending = null
            }
        }
    }

    fun speak(text: String) {
        if (text.isBlank()) return
        if (!ready) { pending = text; return }
        tts?.speak(text, TextToSpeech.QUEUE_FLUSH, null, "ragent_tts")
    }

    fun stop() {
        tts?.stop()
        pending = null
    }

    fun shutdown() {
        tts?.stop()
        tts?.shutdown()
        tts = null
        ready = false
    }
}
