package com.ragent.shopping

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import com.ragent.shopping.data.local.DeviceId
import com.ragent.shopping.data.local.SessionPrefs
import com.ragent.shopping.ui.theme.RAGentTheme

class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        SessionPrefs.init(this)
        DeviceId.init(this)
        enableEdgeToEdge()
        setContent {
            RAGentTheme {
                AppNavigation()
            }
        }
    }
}
