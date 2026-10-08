package io.github.penpaper0878.pdf2md.ocr

import io.github.penpaper0878.pdf2md.core.Answer
import io.github.penpaper0878.pdf2md.core.EngineError
import io.github.penpaper0878.pdf2md.core.EngineUnavailable
import io.github.penpaper0878.pdf2md.core.PageImageSource
import io.github.penpaper0878.pdf2md.core.VisionModel
import java.io.File

/**
 * A vision model running on the phone itself (llama.cpp through JNI): no
 * network at all. Slower than a computer, but it goes wherever the phone goes.
 *
 * [cancelled] is polled while the model works, so Cancel stops it mid-page;
 * [status] hears what it is doing ("looking at the page", "writing…").
 */
class LocalModel(
    private val model: File,
    private val mmproj: File,
    private val libDir: String,
    private val cancelled: () -> Boolean = { false },
    private val status: (String) -> Unit = {},
    private val threads: Int = defaultThreads(),
    private val maxSide: Int = 1280,
    private val maxTokens: Int = 3072,
) : VisionModel, AutoCloseable {
    private var handle = 0L

    override val signature: String get() = "llama.cpp|${model.name}|${mmproj.name}|$maxSide"
    override val description: String get() = "${model.nameWithoutExtension} on this phone"

    /** Load the model into memory (a few seconds); throws if it cannot run here. */
    fun open() {
        if (handle != 0L) return
        if (!NativeLlm.loaded) throw EngineUnavailable("the on-phone AI engine is not part of this build")
        if (!model.exists() || !mmproj.exists()) throw EngineUnavailable("the AI model is not downloaded yet")
        NativeLlm.init(libDir)
        handle = NativeLlm.load(model.path, mmproj.path, threads, CONTEXT)
        if (handle == 0L) {
            val why = String(NativeLlm.lastError(), Charsets.UTF_8).ifBlank { "unknown error" }
            throw EngineUnavailable("the AI model could not be loaded: $why")
        }
    }

    override fun ask(image: PageImageSource, prompt: String, retry: Boolean): Answer {
        open()
        val (w, h, rgb) = image.rgb(maxSide)
        val h0 = handle
        NativeLlm.cancel(h0, false)
        // Watches for Cancel and reports progress while the native call runs.
        val watcher = Thread {
            try {
                while (!Thread.currentThread().isInterrupted) {
                    if (cancelled()) NativeLlm.cancel(h0, true)
                    val p = NativeLlm.progress(h0)
                    when ((p ushr 32).toInt()) {
                        1 -> status("looking at the page…")
                        2 -> status("writing… ${(p and 0xffffffffL)} tokens")
                    }
                    Thread.sleep(500)
                }
            } catch (_: InterruptedException) {
            }
        }.apply { isDaemon = true; name = "pdf2md-model-watch" }
        watcher.start()
        val out = try {
            NativeLlm.transcribe(
                h0, rgb, w, h, prompt.toByteArray(Charsets.UTF_8), maxTokens,
                if (retry) 1.15f else 1.0f, if (retry) 0.2f else 0.0f,
            )
        } finally {
            watcher.interrupt()
            watcher.join()
        }
        val text = String(out, 1, out.size - 1, Charsets.UTF_8)
        return when (out[0].toInt()) {
            DONE -> Answer(text, cutOff = false)
            CUT_OFF -> Answer(text, cutOff = true)
            CANCELLED -> throw InterruptedException()
            else -> throw EngineError("the on-phone model failed on this page: $text")
        }
    }

    override fun close() {
        if (handle != 0L) {
            NativeLlm.free(handle)
            handle = 0L
        }
    }

    companion object {
        /** Image (up to about 2,300 tokens at 1280 px), prompt and a full page of text. */
        const val CONTEXT = 6144

        // Status bytes from the native side (pdf2md::Reply::Status).
        private const val DONE = 0
        private const val CUT_OFF = 1
        private const val CANCELLED = 2

        fun defaultThreads(): Int = maxOf(2, minOf(6, Runtime.getRuntime().availableProcessors() - 2))
    }
}

/** The JNI bridge to llama.cpp (libpdf2md_llm.so); text crosses as UTF-8 bytes. */
object NativeLlm {
    val loaded: Boolean = try {
        System.loadLibrary("pdf2md_llm")
        true
    } catch (e: Throwable) {
        false
    }

    /** Loads the CPU code that suits this phone from [libDir]; once per process. */
    @JvmStatic external fun init(libDir: String)
    @JvmStatic external fun load(modelPath: String, mmprojPath: String, threads: Int, context: Int): Long
    @JvmStatic external fun lastError(): ByteArray

    /** A status byte, then the UTF-8 text (or the error). */
    @JvmStatic external fun transcribe(
        handle: Long, rgb: ByteArray, width: Int, height: Int, prompt: ByteArray,
        maxTokens: Int, repeatPenalty: Float, temperature: Float,
    ): ByteArray

    @JvmStatic external fun cancel(handle: Long, on: Boolean)

    /** Stage (0 idle, 1 looking at the image, 2 writing) in the high 32 bits, tokens written in the low. */
    @JvmStatic external fun progress(handle: Long): Long
    @JvmStatic external fun free(handle: Long)
}
