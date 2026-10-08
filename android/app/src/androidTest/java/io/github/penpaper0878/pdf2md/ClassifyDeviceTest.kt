package io.github.penpaper0878.pdf2md

import androidx.test.ext.junit.runners.AndroidJUnit4
import io.github.penpaper0878.pdf2md.core.Classify
import io.github.penpaper0878.pdf2md.pdf.MuPdfDocument
import io.github.penpaper0878.pdf2md.pdf.MuPdfStats
import org.junit.Assert.assertEquals
import org.junit.Test
import org.junit.runner.RunWith

@RunWith(AndroidJUnit4::class)
class ClassifyDeviceTest {
    private fun kinds(asset: String): List<String> =
        MuPdfDocument(Assets.file(asset).path).use { doc ->
            (1..doc.pageCount).map { n -> doc.withPage(n) { Classify.decide(MuPdfStats.measure(it, n)).kind } }
        }

    @Test fun typedPages() = assertEquals(listOf("digital", "digital", "digital"), kinds("parity/digital.pdf"))
    @Test fun scannedPage() = assertEquals(listOf("scanned"), kinds("scanned.pdf"))
    @Test fun scannerOcrLayerIsIgnored() = assertEquals(listOf("scanned"), kinds("hidden-ocr-layer.pdf"))
    @Test fun vectorInkIsHandwriting() = assertEquals(listOf("scanned"), kinds("ink.pdf"))
    @Test fun inkAnnotationOnTypedPageIsMixed() = assertEquals(listOf("mixed"), kinds("annotated.pdf"))
    @Test fun blankPage() = assertEquals(listOf("empty"), kinds("blank.pdf"))
}
