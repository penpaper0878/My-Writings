package io.github.penpaper0878.pdf2md.ui

import android.app.Application
import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.graphics.ImageDecoder
import android.net.Uri
import android.os.Build
import android.provider.OpenableColumns
import androidx.core.content.FileProvider
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import io.github.penpaper0878.pdf2md.convert.ConversionCancelled
import io.github.penpaper0878.pdf2md.convert.ConversionResult
import io.github.penpaper0878.pdf2md.convert.Converter
import io.github.penpaper0878.pdf2md.convert.ModelStore
import io.github.penpaper0878.pdf2md.convert.OcrCache
import io.github.penpaper0878.pdf2md.convert.PasswordNeeded
import io.github.penpaper0878.pdf2md.convert.Progress
import io.github.penpaper0878.pdf2md.convert.Settings
import io.github.penpaper0878.pdf2md.convert.SettingsStore
import io.github.penpaper0878.pdf2md.core.EngineUnavailable
import io.github.penpaper0878.pdf2md.core.RemoteModel
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import java.io.File

sealed interface Stage {
    data object Idle : Stage
    data class Working(val title: String, val progress: Progress) : Stage
    data class Done(val title: String, val result: ConversionResult) : Stage
    data class Failed(val title: String, val message: String) : Stage
    data class NeedPassword(val title: String, val wrong: Boolean) : Stage
}

data class UiState(
    val settings: Settings,
    val stage: Stage,
    val model: ModelStore.Status,
    val computerCheck: String? = null,
)

class AppViewModel(app: Application) : AndroidViewModel(app) {
    private val store = SettingsStore(app)
    private val models = ModelStore(app)
    private val cache = OcrCache(File(app.cacheDir, "ocr"))

    private val _state = MutableStateFlow(UiState(store.load(), Stage.Idle, models.status()))
    val state: StateFlow<UiState> = _state.asStateFlow()

    private var job: Job? = null
    @Volatile private var cancelRequested = false
    private var inputs: List<Uri> = emptyList()

    val modelSizeMb: Long get() = models.totalBytes / (1024 * 1024)

    fun updateSettings(transform: (Settings) -> Settings) {
        val s = transform(_state.value.settings)
        store.save(s)
        _state.update { it.copy(settings = s, computerCheck = null) }
    }

    fun convert(uris: List<Uri>, password: String? = null) {
        if (uris.isEmpty() || job?.isActive == true) return
        inputs = uris
        cancelRequested = false
        val ctx = getApplication<Application>()
        val title = displayName(uris.first())
        _state.update { it.copy(stage = Stage.Working(title, Progress(0, 0, "Opening…"))) }
        job = viewModelScope.launch {
            val stage = withContext(Dispatchers.IO) {
                try {
                    val converter = Converter(
                        _state.value.settings, cache, models,
                        progress = { p -> _state.update { s -> s.copy(stage = Stage.Working(title, p)) } },
                        cancelled = { cancelRequested },
                    )
                    val pdfs = uris.filter { isPdf(it) }
                    val result = when {
                        pdfs.size == 1 && uris.size == 1 -> converter.convertPdf(copyToCache(uris[0]), password)
                        pdfs.isEmpty() -> converter.convertImages(uris.map { u -> { decodeImage(u) } })
                        else -> throw IllegalArgumentException("Choose one PDF at a time (or several photos).")
                    }
                    Stage.Done(title, result)
                } catch (e: PasswordNeeded) {
                    Stage.NeedPassword(title, e.wrong)
                } catch (e: ConversionCancelled) {
                    Stage.Idle
                } catch (e: OutOfMemoryError) {
                    Stage.Failed(title, "The phone ran out of memory on this document. Try fewer pages at a time (Options → Pages).")
                } catch (e: Exception) {
                    Stage.Failed(title, e.message ?: e.toString())
                } finally {
                    ctx.cacheDir.resolve("input.pdf").delete()
                }
            }
            _state.update { it.copy(stage = stage) }
        }
    }

    fun retryWithPassword(password: String) = convert(inputs, password)

    fun cancel() {
        cancelRequested = true
    }

    fun reset() = _state.update { it.copy(stage = Stage.Idle) }

    fun refreshModel() = _state.update { it.copy(model = models.status()) }

    fun downloadModel(wifiOnly: Boolean) {
        models.download(wifiOnly)
        refreshModel()
    }

    fun removeModel() {
        models.remove()
        refreshModel()
    }

    fun testComputer() {
        val s = _state.value.settings
        _state.update { it.copy(computerCheck = "Checking…") }
        viewModelScope.launch {
            val msg = withContext(Dispatchers.IO) {
                try {
                    val m = RemoteModel(s.computerModel, baseUrl = s.computerUrl.ifBlank { null })
                    m.check()
                    "Connected: ${m.description}" + (m.notes.firstOrNull()?.let { "\n$it" } ?: "")
                } catch (e: EngineUnavailable) {
                    e.message ?: "not reachable"
                } catch (e: Exception) {
                    "Not reachable: ${e.message}"
                }
            }
            _state.update { it.copy(computerCheck = msg) }
        }
    }

    // ------------------------------------------------------------- output

    private val result: ConversionResult? get() = (_state.value.stage as? Stage.Done)?.result

    fun markdown(): String = result?.markdown ?: ""

    fun suggestedName(): String {
        val title = (_state.value.stage as? Stage.Done)?.title ?: "notes"
        return title.substringBeforeLast('.').ifBlank { "notes" } + ".md"
    }

    fun writeTo(uri: Uri): Boolean = runCatching {
        getApplication<Application>().contentResolver.openOutputStream(uri, "wt")!!.use { it.write(markdown().toByteArray()) }
    }.isSuccess

    fun shareFile(): Uri {
        val ctx = getApplication<Application>()
        val dir = File(ctx.cacheDir, "share").apply { mkdirs() }
        val f = File(dir, suggestedName())
        f.writeText(markdown())
        return FileProvider.getUriForFile(ctx, ctx.packageName + ".files", f)
    }

    // ------------------------------------------------------------- inputs

    private fun displayName(uri: Uri): String {
        val ctx = getApplication<Application>()
        runCatching {
            ctx.contentResolver.query(uri, arrayOf(OpenableColumns.DISPLAY_NAME), null, null, null)?.use { c ->
                if (c.moveToFirst()) return c.getString(0)
            }
        }
        return uri.lastPathSegment?.substringAfterLast('/') ?: "document"
    }

    private fun isPdf(uri: Uri): Boolean {
        val type = getApplication<Application>().contentResolver.getType(uri)
        return type == "application/pdf" || displayName(uri).lowercase().endsWith(".pdf")
    }

    private fun copyToCache(uri: Uri): File {
        val ctx = getApplication<Application>()
        val f = File(ctx.cacheDir, "input.pdf")
        ctx.contentResolver.openInputStream(uri)!!.use { input -> f.outputStream().use { input.copyTo(it) } }
        return f
    }

    /** A photo of a page, upright (EXIF), at most ~3000 px on its long side. */
    private fun decodeImage(uri: Uri): Bitmap {
        val ctx = getApplication<Application>()
        val maxSide = 3000
        if (Build.VERSION.SDK_INT >= 28) {
            val src = ImageDecoder.createSource(ctx.contentResolver, uri)
            return ImageDecoder.decodeBitmap(src) { decoder, info, _ ->
                decoder.allocator = ImageDecoder.ALLOCATOR_SOFTWARE
                val longest = maxOf(info.size.width, info.size.height)
                if (longest > maxSide) {
                    val scale = maxSide.toDouble() / longest
                    decoder.setTargetSize((info.size.width * scale).toInt(), (info.size.height * scale).toInt())
                }
            }.copy(Bitmap.Config.ARGB_8888, false)
        }
        val bounds = BitmapFactory.Options().apply { inJustDecodeBounds = true }
        ctx.contentResolver.openInputStream(uri)!!.use { BitmapFactory.decodeStream(it, null, bounds) }
        var sample = 1
        while (maxOf(bounds.outWidth, bounds.outHeight) / sample > maxSide) sample *= 2
        val opts = BitmapFactory.Options().apply { inSampleSize = sample }
        return ctx.contentResolver.openInputStream(uri)!!.use { BitmapFactory.decodeStream(it, null, opts)!! }
    }
}
