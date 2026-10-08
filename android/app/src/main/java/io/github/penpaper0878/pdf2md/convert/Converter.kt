package io.github.penpaper0878.pdf2md.convert

import android.graphics.Bitmap
import io.github.penpaper0878.pdf2md.core.Classify
import io.github.penpaper0878.pdf2md.core.EngineError
import io.github.penpaper0878.pdf2md.core.EngineUnavailable
import io.github.penpaper0878.pdf2md.core.OcrResult
import io.github.penpaper0878.pdf2md.core.PageImage
import io.github.penpaper0878.pdf2md.core.PageKind
import io.github.penpaper0878.pdf2md.core.PageSpec
import io.github.penpaper0878.pdf2md.core.Pipeline
import io.github.penpaper0878.pdf2md.core.RemoteModel
import io.github.penpaper0878.pdf2md.core.Transcriber
import io.github.penpaper0878.pdf2md.ocr.BitmapPageImage
import io.github.penpaper0878.pdf2md.ocr.LocalModel
import io.github.penpaper0878.pdf2md.ocr.PrintedOcr
import io.github.penpaper0878.pdf2md.pdf.MuPdfDocument
import io.github.penpaper0878.pdf2md.pdf.MuPdfStats
import io.github.penpaper0878.pdf2md.pdf.MuPdfText
import com.artifex.mupdf.fitz.Matrix
import com.artifex.mupdf.fitz.android.AndroidDrawDevice
import java.io.File

class ConversionCancelled : Exception("cancelled")

/** The PDF is encrypted; [wrong] if a password was given but did not open it. */
class PasswordNeeded(val wrong: Boolean) : Exception(if (wrong) "wrong password" else "this PDF is password-protected")

data class Progress(val done: Int, val total: Int, val message: String)

class PageOutcome(val number: Int, val kind: String, val method: String, val seconds: Double, val cached: Boolean)

class ConversionResult(
    val markdown: String,
    val pages: List<PageOutcome>,
    val warnings: List<String>,
    val reader: String?,
    val seconds: Double,
) {
    val incomplete: Boolean get() = pages.any { it.method == "missing" || it.method == "error" }

    fun summary(): String {
        val counts = LinkedHashMap<String, Int>()
        for (p in pages) counts[p.method] = (counts[p.method] ?: 0) + 1
        val label = mapOf(
            "text" to "from the text layer", "blank" to "blank", "missing" to "NOT read", "error" to "FAILED",
            "phone" to "read by the on-phone AI", "computer" to "read by your computer", "printed" to "read as printed text",
        )
        val parts = counts.map { (m, n) -> "$n ${label[m] ?: m}" }.toMutableList()
        val cached = pages.count { it.cached }
        if (cached > 0) parts.add("$cached from cache")
        return "${pages.size} page${if (pages.size != 1) "s" else ""}: " + parts.joinToString(", ")
    }
}

/**
 * Converts a PDF (or photos of pages) to Markdown on the phone, routing each
 * page the way the desktop tool does: typed pages from the text layer,
 * scanned and handwritten pages through the chosen reader.
 * Call from one background thread (MuPDF documents are not thread-safe).
 */
class Converter(
    private val settings: Settings,
    private val cache: OcrCache,
    private val models: ModelStore,
    private val progress: (Progress) -> Unit,
    private val cancelled: () -> Boolean,
) {
    private val warnings = mutableListOf<String>()
    private var readerName: String? = null
    private var current = Progress(0, 0, "")

    private fun report(p: Progress) {
        current = p
        progress(p)
    }

    /** What the reader is doing on the current page, after the page's own message. */
    private fun detail(text: String) = progress(current.copy(message = "${current.message} — $text"))

    /** A ready reader for scanned/handwritten pages. */
    private sealed class PageReader {
        abstract val method: String
        abstract val signature: String
        abstract val readsHandwriting: Boolean
        abstract fun read(bitmap: Bitmap, page: Int): OcrResult
        open fun close() {}

        class Model(
            override val method: String,
            private val t: Transcriber,
            private val cancelled: () -> Boolean,
            private val closer: () -> Unit,
        ) : PageReader() {
            override val signature get() = t.signature
            override val readsHandwriting = true
            override fun read(bitmap: Bitmap, page: Int) = t.transcribe(BitmapPageImage(bitmap), page, cancelled)
            override fun close() = closer()
        }

        class Printed(private val ocr: PrintedOcr) : PageReader() {
            override val method = "printed"
            override val signature get() = ocr.signature
            override val readsHandwriting = false
            override fun read(bitmap: Bitmap, page: Int) = ocr.read(bitmap, page)
            override fun close() = ocr.close()
        }
    }

    private var reader: PageReader? = null

    private fun openReader(): PageReader {
        reader?.let { return it }
        val r: PageReader = when (settings.reader) {
            Reader.PHONE -> try {
                if (models.status() != ModelStore.Status.Ready) throw EngineUnavailable("the AI model is not downloaded yet")
                val m = LocalModel(models.modelFile, models.mmprojFile, models.nativeLibDir, cancelled, status = { detail(it) })
                report(Progress(0, 0, "Loading the AI model…"))
                m.open()
                readerName = m.description
                PageReader.Model("phone", Transcriber(m, settings.lang.ifBlank { null }, settings.hint.ifBlank { null }, settings.tiles), cancelled) { m.close() }
            } catch (e: EngineUnavailable) {
                warnings.add("On-phone AI unavailable (${e.message}); scanned pages were read as printed text, which is poor on handwriting.")
                printed()
            }
            Reader.COMPUTER -> try {
                val m = RemoteModel(settings.computerModel, baseUrl = settings.computerUrl.ifBlank { null })
                report(Progress(0, 0, "Connecting to ${m.baseUrl}…"))
                m.check()
                warnings.addAll(m.notes)
                readerName = m.description
                PageReader.Model("computer", Transcriber(m, settings.lang.ifBlank { null }, settings.hint.ifBlank { null }, settings.tiles), cancelled) {}
            } catch (e: EngineUnavailable) {
                warnings.add("Computer unavailable (${e.message}); scanned pages were read as printed text, which is poor on handwriting.")
                printed()
            }
            Reader.PRINTED -> printed()
        }
        reader = r
        return r
    }

    private fun printed(): PageReader {
        val ocr = PrintedOcr(settings.lang)
        readerName = ocr.description
        return PageReader.Printed(ocr)
    }

    private fun checkCancelled() {
        if (cancelled()) throw ConversionCancelled()
    }

    /** Read one page image with the reader, using and filling the cache. */
    private fun ocrPage(bitmap: Bitmap, number: Int, outcome: (String, Boolean, Double) -> Unit): String {
        val started = System.nanoTime()
        val gray = BitmapPageImage(bitmap).gray()
        if (PageImage.isBlank(gray)) {
            outcome("blank", false, 0.0)
            return ""
        }
        val r = openReader()
        val key = cache.key(r.signature, bitmap.width, bitmap.height, gray)
        cache.get(key)?.let {
            warnings.addAll(it.warnings)
            outcome(r.method, true, 0.0)
            return it.markdown
        }
        return try {
            val result = r.read(bitmap, number)
            cache.put(key, result)
            warnings.addAll(result.warnings)
            outcome(r.method, false, (System.nanoTime() - started) / 1e9)
            result.markdown
        } catch (e: InterruptedException) {
            throw ConversionCancelled()
        } catch (e: EngineError) {
            warnings.add("page $number: ${e.message}")
            outcome("error", false, 0.0)
            "<!-- pdf2md: page $number could not be transcribed -->"
        }
    }

    fun convertPdf(file: File, password: String?): ConversionResult {
        val t0 = System.nanoTime()
        try {
            MuPdfDocument(file.path).use { doc ->
                if (doc.needsPassword) {
                    if (password.isNullOrEmpty()) throw PasswordNeeded(false)
                    if (!doc.authenticate(password)) throw PasswordNeeded(true)
                }
                return convertDocument(doc, t0)
            }
        } finally {
            reader?.close()
        }
    }

    private fun convertDocument(doc: MuPdfDocument, t0: Long): ConversionResult {
        val count = doc.pageCount
        val numbers = if (settings.pages.isBlank()) (1..count).toList() else PageSpec.parse(settings.pages, count)
        require(numbers.isNotEmpty()) { "no pages selected (the document has $count)" }

        report(Progress(0, numbers.size, "Looking at the pages…"))
        val kinds = LinkedHashMap<Int, PageKind>()
        for (n in numbers) {
            checkCancelled()
            kinds[n] = doc.withPage(n) { Classify.decide(MuPdfStats.measure(it, n)) }
        }
        val routes = LinkedHashMap<Int, String>()
        for (n in numbers) {
            val kind = kinds.getValue(n).kind
            routes[n] = when {
                kind == "empty" -> "empty"
                settings.mode == "ocr" -> "ocr"
                settings.mode == "digital" -> if (kind == "scanned") "skip" else "text"
                else -> mapOf("digital" to "text", "scanned" to "ocr", "mixed" to "mixed").getValue(kind)
            }
        }
        if (routes.values.any { it == "mixed" }) {
            val handwriting = openReader().readsHandwriting
            for ((n, r) in routes) if (r == "mixed") {
                if (handwriting) routes[n] = "ocr"
                else {
                    routes[n] = "text"
                    warnings.add("page $n: has handwriting next to typed text; only the typed text was converted (choose the AI reader to read both)")
                }
            }
        }

        val results = LinkedHashMap<Int, Pipeline.Page>()
        val outcomes = LinkedHashMap<Int, PageOutcome>()

        // ---- typed pages
        val textPages = numbers.filter { routes[it] == "text" }
        var vocab: Set<String> = emptySet()
        if (textPages.isNotEmpty()) {
            val datas = textPages.map { n ->
                checkCancelled()
                doc.withPage(n) { MuPdfText.extract(it, n) }
            }
            // Typography is measured over the whole document, like the desktop tool.
            val others = (1..count).filter { it !in numbers }.let { o ->
                if (o.size > 40) List(40) { i -> o[(i * o.size / 40.0).toInt()] } else o
            }
            val context = others.mapNotNull { n ->
                val d = doc.withPage(n) { MuPdfText.extract(it, n) }
                d.takeIf { pd -> pd.blocks.sumOf { b -> b.sumOf { l -> l.text.trim().length } } >= 20 }
            }
            val typed = Pipeline.typedPages(datas, context)
            vocab = typed.style.vocab
            for (n in textPages) {
                results[n] = Pipeline.Page(n, typed.markdown.getValue(n), "text")
                outcomes[n] = PageOutcome(n, kinds.getValue(n).kind, "text", 0.0, false)
            }
        }

        // ---- everything else
        val ocrPages = numbers.filter { routes[it] == "ocr" }
        var done = textPages.size
        for (n in numbers) {
            val route = routes.getValue(n)
            if (route == "empty") {
                results[n] = Pipeline.Page(n, "", "blank")
                outcomes[n] = PageOutcome(n, "empty", "blank", 0.0, false)
            } else if (route == "skip") {
                results[n] = Pipeline.Page(n, "<!-- pdf2md: page $n is a scanned image and was not read (typed-pages-only mode) -->", "missing")
                outcomes[n] = PageOutcome(n, kinds.getValue(n).kind, "missing", 0.0, false)
            }
        }
        for (n in ocrPages) {
            checkCancelled()
            done += 1
            report(Progress(done, numbers.size, "Page $n: ${kinds.getValue(n).reason}"))
            val bitmap = doc.withPage(n) { AndroidDrawDevice.drawPage(it, Matrix(200f / 72f)) }
            var method = "error"
            var outcome: PageOutcome? = null
            val md = try {
                ocrPage(bitmap, n) { m, cached, secs ->
                    method = m
                    outcome = PageOutcome(n, kinds.getValue(n).kind, m, secs, cached)
                }
            } finally {
                bitmap.recycle()
            }
            results[n] = Pipeline.Page(n, md, method)
            outcomes[n] = outcome ?: PageOutcome(n, kinds.getValue(n).kind, method, 0.0, false)
        }

        val pages = numbers.map { results.getValue(it) }
        val markdown = Pipeline.document(pages, vocab, settings.pageMarkers)
        return ConversionResult(markdown, numbers.map { outcomes.getValue(it) }, warnings.distinct(), readerName, (System.nanoTime() - t0) / 1e9)
    }

    /** Photos or scans of pages: every one is read by the reader. */
    fun convertImages(images: List<() -> Bitmap>): ConversionResult {
        val t0 = System.nanoTime()
        try {
            val results = mutableListOf<Pipeline.Page>()
            val outcomes = mutableListOf<PageOutcome>()
            for ((i, load) in images.withIndex()) {
                checkCancelled()
                val n = i + 1
                report(Progress(n, images.size, "Photo $n of ${images.size}"))
                val bitmap = load()
                var method = "error"
                var outcome: PageOutcome? = null
                val md = try {
                    ocrPage(bitmap, n) { m, cached, secs ->
                        method = m
                        outcome = PageOutcome(n, "scanned", m, secs, cached)
                    }
                } finally {
                    bitmap.recycle()
                }
                results.add(Pipeline.Page(n, md, method))
                outcomes.add(outcome ?: PageOutcome(n, "scanned", method, 0.0, false))
            }
            val markdown = Pipeline.document(results, emptySet(), settings.pageMarkers)
            return ConversionResult(markdown, outcomes, warnings.distinct(), readerName, (System.nanoTime() - t0) / 1e9)
        } finally {
            reader?.close()
        }
    }
}
