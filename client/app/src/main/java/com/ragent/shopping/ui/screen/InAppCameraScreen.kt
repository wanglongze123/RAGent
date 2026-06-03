package com.ragent.shopping.ui.screen

import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.graphics.Matrix
import androidx.camera.core.*
import androidx.camera.lifecycle.ProcessCameraProvider
import androidx.camera.view.PreviewView
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.interaction.MutableInteractionSource
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalLifecycleOwner
import androidx.compose.ui.unit.dp
import androidx.compose.ui.viewinterop.AndroidView
import androidx.core.content.ContextCompat
import com.ragent.shopping.ui.theme.BrandIndigo
import java.nio.ByteBuffer
import java.util.concurrent.Executors

@Composable
fun InAppCameraScreen(
    onImageCaptured: (Bitmap) -> Unit,
    onBack: () -> Unit,
) {
    val context        = LocalContext.current
    val lifecycleOwner = LocalLifecycleOwner.current
    val cameraExecutor = remember { Executors.newSingleThreadExecutor() }

    // 保存 provider 引用，在离开页面时必须 unbindAll，否则相机持续吃 CPU
    var cameraProvider: ProcessCameraProvider? by remember { mutableStateOf(null) }
    var imageCapture: ImageCapture? by remember { mutableStateOf(null) }
    var isCapturing by remember { mutableStateOf(false) }

    // 页面销毁时：停相机、关线程池
    DisposableEffect(Unit) {
        onDispose {
            cameraProvider?.unbindAll()   // ← 关键：释放相机
            cameraExecutor.shutdown()
        }
    }

    Box(modifier = Modifier.fillMaxSize().background(Color.Black)) {

        // ── CameraX 预览 ──────────────────────────────────
        AndroidView(
            modifier = Modifier.fillMaxSize(),
            factory = { ctx ->
                val previewView = PreviewView(ctx).apply {
                    implementationMode = PreviewView.ImplementationMode.COMPATIBLE
                }
                ProcessCameraProvider.getInstance(ctx).also { future ->
                    future.addListener({
                        val provider = future.get()
                        cameraProvider = provider          // 保存引用供 DisposableEffect 使用

                        val preview = Preview.Builder().build().also {
                            it.surfaceProvider = previewView.surfaceProvider
                        }
                        val capture = ImageCapture.Builder()
                            .setCaptureMode(ImageCapture.CAPTURE_MODE_MINIMIZE_LATENCY)
                            .build()
                        imageCapture = capture

                        fun bind(selector: CameraSelector) {
                            provider.unbindAll()
                            provider.bindToLifecycle(lifecycleOwner, selector, preview, capture)
                        }
                        try {
                            bind(CameraSelector.DEFAULT_BACK_CAMERA)
                        } catch (_: Exception) {
                            try { bind(CameraSelector.DEFAULT_FRONT_CAMERA) } catch (_: Exception) {}
                        }
                    }, ContextCompat.getMainExecutor(ctx))
                }
                previewView
            },
        )

        // ── 左上角返回 ────────────────────────────────────
        IconButton(
            onClick = onBack,
            modifier = Modifier
                .padding(16.dp)
                .align(Alignment.TopStart)
                .size(44.dp)
                .clip(CircleShape)
                .background(Color.Black.copy(alpha = 0.45f)),
        ) {
            Icon(Icons.AutoMirrored.Filled.ArrowBack, "返回", tint = Color.White)
        }

        // ── 底部快门按钮 ──────────────────────────────────
        Box(
            modifier = Modifier
                .align(Alignment.BottomCenter)
                .fillMaxWidth()
                .padding(bottom = 48.dp),
            contentAlignment = Alignment.Center,
        ) {
            Box(
                modifier = Modifier
                    .size(80.dp)
                    .clip(CircleShape)
                    .border(3.dp, Color.White, CircleShape),
                contentAlignment = Alignment.Center,
            ) {
                Box(
                    modifier = Modifier
                        .size(62.dp)
                        .clip(CircleShape)
                        .background(if (isCapturing) BrandIndigo else Color.White)
                        .let { if (!isCapturing) it.shutterClick {
                            isCapturing = true
                            val ic = imageCapture ?: run { isCapturing = false; return@shutterClick }
                            ic.takePicture(cameraExecutor, object : ImageCapture.OnImageCapturedCallback() {
                                override fun onCaptureSuccess(image: ImageProxy) {
                                    val bmp = imageProxyToBitmap(image)
                                    image.close()
                                    ContextCompat.getMainExecutor(context).execute {
                                        if (bmp != null) onImageCaptured(bmp) else isCapturing = false
                                    }
                                }
                                override fun onError(e: ImageCaptureException) {
                                    ContextCompat.getMainExecutor(context).execute { isCapturing = false }
                                }
                            })
                        } else it },
                )
            }
        }
    }
}

private fun imageProxyToBitmap(image: ImageProxy): Bitmap? {
    val buffer: ByteBuffer = image.planes[0].buffer
    val bytes = ByteArray(buffer.remaining()).also { buffer.get(it) }
    val bmp = BitmapFactory.decodeByteArray(bytes, 0, bytes.size) ?: return null
    val rot = image.imageInfo.rotationDegrees
    return if (rot == 0) bmp else {
        val m = Matrix().apply { postRotate(rot.toFloat()) }
        Bitmap.createBitmap(bmp, 0, 0, bmp.width, bmp.height, m, true)
    }
}

@Composable
private fun Modifier.shutterClick(onClick: () -> Unit): Modifier =
    this.clickable(
        interactionSource = remember { MutableInteractionSource() },
        indication = null,
        onClick = onClick,
    )
