package io.github.penpaper0878.pdf2md.core

/**
 * How pages become one document, mirroring the desktop converter:
 * typography and running headers are measured across the document, each
 * typed page is rendered, OCR'd pages are tidied, and the pages are joined.
 */
object Pipeline {

    class TypedResult(val style: DocStyle, val markdown: Map<Int, String>)

    /**
     * Markdown for the typed (text-layer) pages. [context] are extra pages of
     * the same document that were not selected, used only to measure
     * typography, so a page converts the same whichever pages were chosen.
     */
    fun typedPages(
        pages: List<PageData>,
        context: List<PageData> = emptyList(),
        stripHeaders: Boolean = true,
        tables: Map<Int, List<Digital.Placed>> = emptyMap(),
        images: Map<Int, List<Digital.Placed>> = emptyMap(),
    ): TypedResult {
        val all = pages + context
        val style = DocStyle(all)
        if (stripHeaders) {
            val running = Digital.findRunningLines(all, style.bodySize)
            for (p in pages) Digital.stripRunning(p, running, style.bodySize)
        }
        val out = LinkedHashMap<Int, String>()
        for (p in pages) {
            out[p.number] = Digital.pageMarkdown(
                p, style, tables = tables[p.number].orEmpty(), images = images[p.number].orEmpty(),
            )
        }
        return TypedResult(style, out)
    }

    /** One page of the result, however it was read. */
    class Page(val number: Int, var markdown: String, val method: String)

    /**
     * Join pages into the final document. [vocab] is the typed pages'
     * vocabulary; the OCR'd pages' words are added for de-hyphenation.
     */
    fun document(pages: List<Page>, vocab: Set<String>, pageMarkers: Boolean = false, stripHeaders: Boolean = true): String {
        if (stripHeaders) {
            val ocr = pages.filter { it.method !in setOf("text", "blank", "missing", "error") }
            val wrapped = ocr.map { EdgeStrip.OcrPage(it.markdown, it.method == "printed") }
            EdgeStrip.strip(wrapped)
            ocr.zip(wrapped).forEach { (p, w) -> p.markdown = w.markdown }
        }
        if (pageMarkers) {
            return pages.joinToString("\n\n") { "<!-- page ${it.number} -->\n\n${it.markdown}".trimEnd() }.trim() + "\n"
        }
        val words = vocab.toMutableSet()
        words.addAll(MdText.buildVocab(pages.filter { it.method != "text" }.map { it.markdown }))
        return MdText.joinChunks(pages.map { it.markdown }, words)
    }
}
