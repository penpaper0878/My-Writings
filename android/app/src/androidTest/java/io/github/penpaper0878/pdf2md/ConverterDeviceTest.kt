package io.github.penpaper0878.pdf2md

import androidx.test.ext.junit.runners.AndroidJUnit4
import com.artifex.mupdf.fitz.Matrix
import com.artifex.mupdf.fitz.android.AndroidDrawDevice
import io.github.penpaper0878.pdf2md.convert.Converter
import io.github.penpaper0878.pdf2md.convert.ModelStore
import io.github.penpaper0878.pdf2md.convert.OcrCache
import io.github.penpaper0878.pdf2md.convert.Reader
import io.github.penpaper0878.pdf2md.convert.Settings
import io.github.penpaper0878.pdf2md.ocr.PrintedOcr
import io.github.penpaper0878.pdf2md.pdf.MuPdfDocument
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import org.junit.runner.RunWith
import java.io.File

@RunWith(AndroidJUnit4::class)
class ConverterDeviceTest {
    private fun converter(settings: Settings) = Converter(
        settings,
        OcrCache(File(Assets.target.cacheDir, "test-ocr-" + System.nanoTime())),
        ModelStore(Assets.target),
        progress = {},
        cancelled = { false },
    )

    @Test
    fun printedOcrReadsAScannedPage() {
        val bitmap = MuPdfDocument(Assets.file("scanned.pdf").path).use { doc ->
            doc.withPage(1) { AndroidDrawDevice.drawPage(it, Matrix(200f / 72f)) }
        }
        val md = PrintedOcr(null).use { it.read(bitmap, 1).markdown }
        assertTrue(md, "Photosynthesis" in md)
        assertTrue(md, "Light reactions happen in the thylakoids" in md)
    }

    @Test
    fun typedPdf() {
        val r = converter(Settings(reader = Reader.PRINTED)).convertPdf(Assets.file("parity/digital.pdf"), null)
        val md = r.markdown
        assertEquals(listOf("text", "text", "text"), r.pages.map { it.method })
        assertTrue(md, md.startsWith("# Quarterly Notes\n"))
        assertTrue(md, "[full data](https://example.org/data)" in md)
        assertTrue(md, "- Costs stayed flat\n  - Except for shipping" in md)
        assertTrue(md, "ACME Quarterly Report" !in md)
    }

    @Test
    fun scannedPdfWithPrintedReader() {
        val r = converter(Settings(reader = Reader.PRINTED)).convertPdf(Assets.file("scanned.pdf"), null)
        assertEquals(listOf("printed"), r.pages.map { it.method })
        assertTrue(r.markdown, "Photosynthesis" in r.markdown)
    }

    @Test
    fun handwritingNeedsAnAiReader() {
        val r = converter(Settings(reader = Reader.PRINTED)).convertPdf(Assets.file("annotated.pdf"), null)
        assertEquals(listOf("text"), r.pages.map { it.method })
        assertTrue(r.warnings.joinToString(), r.warnings.any { "handwriting" in it })
    }

    @Test
    fun missingPhoneModelFallsBackWithAWarning() {
        val r = converter(Settings(reader = Reader.PHONE)).convertPdf(Assets.file("scanned.pdf"), null)
        assertEquals(listOf("printed"), r.pages.map { it.method })
        assertTrue(r.warnings.joinToString(), r.warnings.any { "On-phone AI unavailable" in it })
    }

    @Test
    fun pageSelectionAndMarkers() {
        val r = converter(Settings(reader = Reader.PRINTED, pages = "2", pageMarkers = true))
            .convertPdf(Assets.file("parity/digital.pdf"), null)
        assertTrue(r.markdown, r.markdown.startsWith("<!-- page 2 -->\n\n## 2. Two Columns"))
    }
}
