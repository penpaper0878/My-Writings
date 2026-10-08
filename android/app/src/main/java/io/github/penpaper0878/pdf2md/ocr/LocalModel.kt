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
 */
class LocalModel(
    private val model: File,
    private val mmproj: File,
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
        handle = NativeLlm.load(model.path, mmproj.path, threads, 8192)
        if (handle == 0L) throw EngineUnavailable("the AI model could not be loaded (not enough memory?)")
    }

    override fun ask(image: PageImageSource, prompt: String, retry: Boolean): Answer {
        open()
        val (w, h, rgb) = image.rgb(maxSide)
        val out = NativeLlm.transcribe(handle, rgb, w, h, prompt, maxTokens, if (retry) 1.15f else 1.0f)
            ?: throw EngineError("the on-phone model failed on this page")
        // The native side marks a reply that ran out of room with a leading \u0001.
        val cut = out.startsWith("\u0001")
        return Answer(if (cut) out.substring(1) else out, cut)
    }

    override fun close() {
        if (handle != 0L) {
            NativeLlm.free(handle)
            handle = 0L
        }
    }

    companion object {
        fun defaultThreads(): Int = maxOf(2, minOf(6, Runtime.getRuntime().availableProcessors() - 2))
    }
}

/** The JNI bridge to llama.cpp (libpdf2md_llm.so). */
object NativeLlm {
    val loaded: Boolean = try {
        System.loadLibrary("pdf2md_llm")
        true
    } catch (e: Throwable) {
        false
    }

    @JvmStatic external fun load(modelPath: String, mmprojPath: String, threads: Int, context: Int): Long
    @JvmStatic external fun transcribe(handle: Long, rgb: ByteArray, width: Int, height: Int, prompt: String, maxTokens: Int, repeatPenalty: Float): String?
    @JvmStatic external fun free(handle: Long)
}
