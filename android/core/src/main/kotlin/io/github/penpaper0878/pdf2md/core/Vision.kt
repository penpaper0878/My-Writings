package io.github.penpaper0878.pdf2md.core

/** The engine cannot run (not installed, not reachable, model missing): the message says how to fix it. */
class EngineUnavailable(message: String) : Exception(message)

/** The engine ran but failed on a page. */
class EngineError(message: String, cause: Throwable? = null) : Exception(message, cause)

class OcrResult(val markdown: String, val warnings: List<String> = emptyList())

/** A page image, independent of the platform's bitmap type. */
interface PageImageSource {
    val width: Int
    val height: Int

    /** One byte per pixel, row by row. */
    fun gray(): ByteArray

    /** Rows [top, bottom) as a new image. */
    fun crop(top: Int, bottom: Int): PageImageSource

    /** PNG bytes, scaled down so the longest side is at most [maxSide]. */
    fun png(maxSide: Int): ByteArray

    /** Packed RGB bytes (3 per pixel) scaled to at most [maxSide]: returns (width, height, bytes). */
    fun rgb(maxSide: Int): Triple<Int, Int, ByteArray>
}

/** One answer from a vision model. [cutOff]: it stopped because it ran out of room. */
class Answer(val text: String, val cutOff: Boolean)

/** A vision-language model that can read one image. */
interface VisionModel {
    /** Everything that changes the output, for the cache key. */
    val signature: String
    val description: String

    fun ask(image: PageImageSource, prompt: String, retry: Boolean): Answer
}

/**
 * Reads a page with a vision model the way the desktop tool does: whole page
 * (or N strips), and if the model loops or is cut off, again in more strips
 * with repeats discouraged. Port of VlmEngine.transcribe.
 */
class Transcriber(
    private val model: VisionModel,
    private val lang: String? = null,
    private val hint: String? = null,
    private val tiles: Int = 1,
) {
    val signature: String get() = listOf("vlm", Prompt.VERSION, model.signature, lang, hint, tiles).joinToString("|")

    private fun strips(image: PageImageSource, parts: Int): List<PageImageSource> {
        if (parts <= 1) return listOf(image)
        val cuts = PageImage.findCuts(image.gray(), image.width, image.height, parts)
        val edges = listOf(0) + cuts + listOf(image.height)
        return edges.zipWithNext().map { (top, bottom) -> image.crop(top, bottom) }
    }

    private fun read(image: PageImageSource, parts: Int, retry: Boolean, cancelled: () -> Boolean): Pair<String, Boolean> {
        val pieces = strips(image, parts)
        val texts = mutableListOf<String>()
        var bad = false
        for ((i, strip) in pieces.withIndex()) {
            if (cancelled()) throw InterruptedException()
            val answer = model.ask(strip, Prompt.build(i + 1, pieces.size, lang, hint), retry)
            val text = OcrCleanup.cleanModelOutput(answer.text)
            bad = bad || answer.cutOff || OcrCleanup.looksDegenerate(text)
            texts.add(text)
        }
        return MdText.joinChunks(texts, mergeTables = "any") to bad
    }

    fun transcribe(image: PageImageSource, pageNumber: Int, cancelled: () -> Boolean = { false }): OcrResult {
        val warnings = mutableListOf<String>()
        var (text, bad) = read(image, tiles, retry = false, cancelled)
        if (bad) {
            // Looping or cut off: look again in smaller pieces, gently discouraging repeats.
            val parts = minOf(maxOf(2, tiles * 2), 8)
            val (text2, bad2) = read(image, parts, retry = true, cancelled)
            if (!bad2) {
                text = text2
                bad = false
            } else {
                // Both went wrong: keep whichever says more once the loops are removed.
                val first = OcrCleanup.collapseRepetition(text)
                val second = OcrCleanup.collapseRepetition(text2)
                text = if (second.length > first.length) second else first
            }
        }
        if (bad) {
            warnings.add(
                "page $pageNumber: the model repeated itself or ran out of room; the transcription may be incomplete",
            )
        }
        return OcrResult(text, warnings)
    }
}
