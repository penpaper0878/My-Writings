package io.github.penpaper0878.pdf2md.ocr

import android.graphics.Bitmap
import com.google.android.gms.tasks.Tasks
import com.google.mlkit.vision.common.InputImage
import com.google.mlkit.vision.text.TextRecognition
import com.google.mlkit.vision.text.TextRecognizer
import com.google.mlkit.vision.text.devanagari.DevanagariTextRecognizerOptions
import com.google.mlkit.vision.text.latin.TextRecognizerOptions
import io.github.penpaper0878.pdf2md.core.OcrLayout
import io.github.penpaper0878.pdf2md.core.OcrResult
import io.github.penpaper0878.pdf2md.core.OcrWord

/**
 * Printed-text OCR with Google ML Kit, on the phone and offline (the models
 * are inside the app). Fast and accurate on clean print; not meant for
 * handwriting. Words and their boxes go through the same layout logic as the
 * desktop tool's Tesseract output.
 */
class PrintedOcr(lang: String?) : AutoCloseable {
    private val codes = (lang ?: "").replace(",", "+").split("+").filter { it.isNotBlank() }.toSet()
    private val devanagari = codes.any { it in setOf("hin", "mar", "san", "nep") }
    private val recognizer: TextRecognizer = TextRecognition.getClient(
        if (devanagari) DevanagariTextRecognizerOptions.Builder().build() else TextRecognizerOptions.DEFAULT_OPTIONS,
    )

    val signature: String = "mlkit-16.0.1|" + (if (devanagari) "devanagari" else "latin")
    val description: String = "ML Kit printed text (" + (if (devanagari) "Devanagari + Latin" else "Latin") + ")"

    fun read(bitmap: Bitmap, pageNumber: Int): OcrResult {
        val text = Tasks.await(recognizer.process(InputImage.fromBitmap(bitmap, 0)))
        val words = mutableListOf<OcrWord>()
        for ((bi, block) in text.textBlocks.withIndex()) {
            for ((li, line) in block.lines.withIndex()) {
                for (el in line.elements) {
                    val box = el.boundingBox ?: continue
                    words.add(OcrWord(el.text, box.left, box.top, box.width(), box.height(), 95.0, bi, 0, li))
                }
            }
        }
        val (md, _) = OcrLayout.toMarkdown(words)
        val warnings = mutableListOf<String>()
        val unsupported = codes.filter { it !in setOf("eng", "hin", "mar", "san", "nep", "fra", "deu", "spa", "ita", "por", "nld") }
        if (unsupported.isNotEmpty()) {
            warnings.add("page $pageNumber: printed-text OCR cannot read ${unsupported.joinToString()}; use the AI reader for those scripts")
        }
        return OcrResult(md, warnings)
    }

    override fun close() = recognizer.close()
}
