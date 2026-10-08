package io.github.penpaper0878.pdf2md

import androidx.test.ext.junit.runners.AndroidJUnit4
import io.github.penpaper0878.pdf2md.core.Box
import io.github.penpaper0878.pdf2md.core.PageData
import io.github.penpaper0878.pdf2md.core.Pipeline
import io.github.penpaper0878.pdf2md.pdf.MuPdfDocument
import io.github.penpaper0878.pdf2md.pdf.MuPdfText
import org.junit.Assert.assertEquals
import org.junit.Assert.fail
import org.junit.Test
import org.junit.runner.RunWith

/**
 * MuPDF on Android must read typed PDFs exactly as PyMuPDF does on the
 * desktop, and the result must be the same Markdown.
 */
@RunWith(AndroidJUnit4::class)
class DeviceParityTest {

    private fun near(a: Box, b: Box) =
        Math.abs(a.x0 - b.x0) < 0.05 && Math.abs(a.y0 - b.y0) < 0.05 && Math.abs(a.x1 - b.x1) < 0.05 && Math.abs(a.y1 - b.y1) < 0.05

    /** The first place the two page data differ, described for a person. */
    private fun firstDifference(name: String, got: List<PageData>, want: List<PageData>): String? {
        if (got.size != want.size) return "$name: ${got.size} pages, desktop ${want.size}"
        for ((g, w) in got.zip(want)) {
            if (Math.abs(g.width - w.width) > 0.01 || Math.abs(g.height - w.height) > 0.01) return "$name p${g.number}: page size"
            val gl = g.blocks.flatten()
            val wl = w.blocks.flatten()
            if (g.blocks.size != w.blocks.size) return "$name p${g.number}: ${g.blocks.size} blocks, desktop ${w.blocks.size}"
            for ((i, pair) in gl.zip(wl).withIndex()) {
                val (a, b) = pair
                if (a.text != b.text) return "$name p${g.number} line $i: '${a.text}' vs desktop '${b.text}'"
                if (!near(a.bbox, b.bbox)) return "$name p${g.number} line $i '${a.text}': box ${a.bbox} vs ${b.bbox}"
                if (a.spans.size != b.spans.size) return "$name p${g.number} line $i '${a.text}': ${a.spans.size} spans vs ${b.spans.size}"
                for ((sa, sb) in a.spans.zip(b.spans)) {
                    if (sa.style() != sb.style() || sa.size != sb.size) {
                        return "$name p${g.number} '${sa.text}': style ${sa.style()} ${sa.size} vs ${sb.style()} ${sb.size}"
                    }
                }
            }
            if (gl.size != wl.size) return "$name p${g.number}: ${gl.size} lines, desktop ${wl.size}"
        }
        return null
    }

    @Test
    fun typedPdfsMatchTheDesktopTool() {
        for (name in listOf("digital", "paper", "slides", "book")) {
            val expected = Assets.fixture(name)
            val pages = MuPdfDocument(Assets.file("parity/$name.pdf").path).use { doc ->
                (1..doc.pageCount).map { n -> doc.withPage(n) { MuPdfText.extract(it, n) } }
            }
            firstDifference(name, pages, expected.pages)?.let { fail(it) }
            val typed = Pipeline.typedPages(pages, tables = expected.tables)
            val md = Pipeline.document(pages.map { Pipeline.Page(it.number, typed.markdown.getValue(it.number), "text") }, typed.style.vocab)
            assertEquals(name, expected.markdown, md)
        }
    }
}
