package io.github.penpaper0878.pdf2md.core

/**
 * Markdown from a page's own text layer: exact characters, structure rebuilt
 * from typography. Port of the structure logic in the desktop `digital.py`;
 * the PDF-reading side lives in the app (MuPDF) and fills [PageData].
 */

/** Typography measured over the whole document. */
class DocStyle(pages: List<PageData>) {
    val bodySize: Double
    val headingSizes: List<Double>
    val vocab: Set<String>

    init {
        val sizes = LinkedHashMap<Double, Double>()
        val texts = mutableListOf<String>()
        for (p in pages) for (block in p.blocks) for (line in block) {
            for (s in line.spans) {
                val n = s.text.trim().length
                if (n > 0) {
                    val key = PyMath.roundHalf(s.size)
                    sizes[key] = (sizes[key] ?: 0.0) + n
                }
            }
            texts.add(line.text)
        }
        bodySize = PyMath.mostCommon(sizes) ?: 11.0
        val bodyChars = if (sizes.isNotEmpty()) sizes.getValue(bodySize) else 1.0
        val threshold = maxOf(bodySize * 1.12, bodySize + 1.0)
        val candidates = sizes.entries
            .filter { (sz, n) -> sz >= threshold && n <= maxOf(bodyChars * 0.35, 400.0) }
            .map { it.key }
            .sortedDescending()
        // Sizes within half a point of each other are the same heading level.
        val levels = mutableListOf<Double>()
        for (sz in candidates) if (levels.isEmpty() || levels.last() - sz >= 0.75) levels.add(sz)
        headingSizes = levels.take(6)
        vocab = MdText.buildVocab(texts)
    }

    fun headingLevel(size: Double): Int {
        for ((i, sz) in headingSizes.withIndex()) if (size >= sz - 0.5) return minOf(i + 1, 6)
        return 0
    }
}

/** An element placed on the page: a text block, or ready-made Markdown (table, image). */
class Item(val kind: String, val bbox: Box, val lines: List<Line> = emptyList(), var markdown: String = "")

class Frag(
    var kind: String, // heading | para | list | code | table | image | title?
    var text: String,
    val x0: Double = 0.0,
    val marker: String = "",
    var bbox: Box? = null,
    val level: Int = 0,
    val alt: String = "",
) {
    fun copy() = Frag(kind, text, x0, marker, bbox, level, alt)
}

object Digital {
    const val MAX_HEADING_CHARS = 160
    private val PAGE_NUMBER_RE = Regex(
        """^(page|p\.|seite|pg\.?)?[\s\p{Z}]*[#ivxlcdm]{1,6}[\s\p{Z}]*((of|/|von)[\s\p{Z}]*[#]+)?$""", RegexOption.IGNORE_CASE,
    )
    private val WS_RE = Regex("""[\s\p{Z}]+""")
    private val DIGITS_RE = Regex("""\p{Nd}+""")

    // ------------------------------------------------ extraction helpers

    /** Insert a space where two spans sit apart but neither carries one. */
    fun spaceSpans(spans: List<Span>): MutableList<Span> {
        val out = mutableListOf<Span>()
        for (s in spans) {
            if (out.isNotEmpty()) {
                val prev = out.last()
                val gap = s.bbox.x0 - prev.bbox.x1
                if (gap > 0.2 * maxOf(s.size, 1.0) && !prev.text.endsWith(" ") && !s.text.startsWith(" ")) prev.text += " "
            }
            out.add(s)
        }
        return out
    }

    /** MuPDF sometimes splits one visual line (tabs, justification) into several. */
    fun mergeSameBaseline(lines: List<Line>): MutableList<Line> {
        val out = mutableListOf<Line>()
        for (ln in lines) {
            if (out.isNotEmpty()) {
                val p = out.last()
                val h = minOf(p.bbox.y1 - p.bbox.y0, ln.bbox.y1 - ln.bbox.y0)
                val overlap = minOf(p.bbox.y1, ln.bbox.y1) - maxOf(p.bbox.y0, ln.bbox.y0)
                if (h > 0 && overlap >= 0.6 * h && ln.bbox.x0 >= p.bbox.x1 - 1) {
                    if (!p.spans.last().text.endsWith(" ")) p.spans.last().text += " "
                    p.spans.addAll(ln.spans)
                    p.bbox = Box.union(listOf(p.bbox, ln.bbox))
                    continue
                }
            }
            out.add(ln)
        }
        return out
    }

    // ------------------------------------------- running headers/footers

    private fun runningKey(line: Line, bodySize: Double): String {
        val text = WS_RE.replace(line.text.trim().lowercase()) { " " }
        if (line.size <= bodySize * 1.05) return DIGITS_RE.replace(text) { "#" }
        return "=$text"
    }

    private fun inMargin(line: Line, page: PageData, margin: Double) =
        line.bbox.y1 <= page.height * margin || line.bbox.y0 >= page.height * (1 - margin)

    /** Keys of lines that repeat in the top/bottom margin of many pages. */
    fun findRunningLines(pages: List<PageData>, bodySize: Double, margin: Double = 0.1): Set<String> {
        if (pages.size < 3) return emptySet()
        val counts = HashMap<String, Int>()
        for (p in pages) {
            val seen = HashSet<String>()
            for (block in p.blocks) for (ln in block) if (inMargin(ln, p, margin)) seen.add(runningKey(ln, bodySize))
            for (k in seen) counts[k] = (counts[k] ?: 0) + 1
        }
        val need = maxOf(3, (pages.size * 0.4 + 0.999).toInt())
        return counts.filter { (t, n) -> n >= need && t.trim('=').isNotEmpty() }.keys
    }

    /** Remove running headers/footers and small page numbers from a page's margins. */
    fun stripRunning(page: PageData, running: Set<String>, bodySize: Double, margin: Double = 0.1) {
        val keptBlocks = mutableListOf<List<Line>>()
        for (block in page.blocks) {
            val kept = block.filter { ln ->
                if (!inMargin(ln, page, margin)) return@filter true
                val key = runningKey(ln, bodySize)
                !(key in running || (!key.startsWith("=") && PAGE_NUMBER_RE.find(key) != null))
            }
            if (kept.isNotEmpty()) keptBlocks.add(kept)
        }
        page.blocks = keptBlocks
    }

    // ------------------------------------------------------------ render

    private fun hasAlnum(text: String) = text.any { it.isLetterOrDigit() }

    private fun linkTarget(uri: String) = uri.replace(" ", "%20").replace("(", "%28").replace(")", "%29")

    /** Spans to inline Markdown, merging runs that share a style. */
    fun renderSpans(spans: List<Span>, plain: Boolean = false): String {
        val runs = mutableListOf<Pair<Style, StringBuilder>>()
        for (s in spans) {
            val st = s.style(plain)
            if (runs.isNotEmpty() && runs.last().first == st &&
                hasAlnum(runs.last().second.toString()) == hasAlnum(s.text)
            ) {
                runs.last().second.append(s.text)
            } else if (runs.isNotEmpty() && s.text.isBlank() && !runs.last().first.mono) {
                runs.last().second.append(s.text) // whitespace joins the previous run
            } else {
                runs.add(st to StringBuilder(s.text))
            }
        }
        val out = StringBuilder()
        for ((style, sb) in runs) {
            val text = sb.toString()
            val core = text.trim()
            if (core.isEmpty()) {
                out.append(text)
                continue
            }
            val lead = text.substring(0, text.length - text.trimStart().length)
            val trail = text.substring(text.trimEnd().length)
            var md: String
            if (style.mono) {
                md = MdText.codeSpan(core)
            } else {
                md = MdText.escapeInline(core)
                var bold = style.bold
                var italic = style.italic
                var strike = style.strike
                if (style.sup) md = "<sup>$md</sup>"
                if (!core.any { it.isLetterOrDigit() }) {
                    // "*{*" or "**,**": emphasis on bare symbols is typesetting noise.
                    bold = false; italic = false; strike = false
                }
                if (strike) md = "~~$md~~"
                md = when {
                    bold && italic -> "***$md***"
                    bold -> "**$md**"
                    italic -> "*$md*"
                    else -> md
                }
            }
            if (style.link != null) md = "[$md](${linkTarget(style.link)})"
            out.append(lead).append(md).append(trail)
        }
        return out.toString()
    }

    private fun introduces(title: Frag, nxt: Frag?): Boolean {
        val tb = title.bbox ?: return false
        val nb = nxt?.bbox ?: return false
        if (nxt.kind != "para" && nxt.kind != "list") return false
        val height = maxOf(tb.y1 - tb.y0, 1.0)
        val gap = nb.y0 - tb.y1
        val dx = nb.x0 - tb.x0
        val aligned = Math.abs(dx) <= 6 || (nxt.kind == "list" && dx >= 0 && dx <= 30)
        return gap >= -1 && gap <= 2.5 * height && aligned
    }

    private fun isBoldTitle(line: Line): Boolean {
        val spans = line.spans.filter { it.text.isNotBlank() }
        val text = line.text.trim()
        return spans.isNotEmpty() &&
            spans.all { it.bold && !it.mono } &&
            text.length in 2..80 &&
            (text[0].isUpperCase() || text[0].isDigit()) &&
            text.last() !in ".,;:!?" &&
            hasAlnum(text)
    }

    /** One span list for a wrapped paragraph, hyphenation undone at the joins. */
    fun joinLineSpans(lines: List<Line>, vocab: Set<String>?): List<Span> {
        val out = mutableListOf<Span>()
        var prevText = ""
        for (ln in lines) {
            val spans = ln.spans.map { it.copy() }.toMutableList()
            while (spans.isNotEmpty() && spans[0].text.isBlank()) spans.removeAt(0)
            if (spans.isEmpty()) continue
            spans[0].text = spans[0].text.trimStart()
            val text = spans.joinToString("") { it.text }
            if (out.isNotEmpty()) {
                while (out.isNotEmpty() && out.last().text.isBlank()) out.removeAt(out.size - 1)
                if (out.isNotEmpty()) {
                    out.last().text = out.last().text.trimEnd()
                    val (drop, sep) = MdText.joiner(prevText, text, vocab)
                    if (drop > 0) out.last().text = out.last().text.dropLast(drop)
                    if (sep.isNotEmpty()) out.last().text += sep
                }
            }
            out.addAll(spans)
            prevText = text
        }
        if (out.isNotEmpty()) out.last().text = out.last().text.trimEnd()
        return out
    }

    private fun dropChars(spans: List<Span>, n0: Int): MutableList<Span> {
        val out = spans.map { it.copy() }.toMutableList()
        var n = n0
        while (n > 0 && out.isNotEmpty()) {
            val s = out[0]
            if (s.text.length <= n) {
                n -= s.text.length
                out.removeAt(0)
            } else {
                s.text = s.text.substring(n)
                n = 0
            }
        }
        if (out.isNotEmpty()) out[0].text = out[0].text.trimStart()
        return out
    }

    class PageRenderer(private val style: DocStyle) {

        fun blockFragments(lines: List<Line>): List<Frag> {
            val frags = mutableListOf<Frag>()
            val left = lines.minOf { it.bbox.x0 }
            val right = lines.maxOf { it.bbox.x1 }
            var i = 0
            val n = lines.size
            var inList = false
            while (i < n) {
                val ln = lines[i]
                val text = ln.text.trim()
                val level = if (!ln.mono) style.headingLevel(ln.size) else 0
                if (level != 0 && text.length <= 200) {
                    val group = mutableListOf(ln)
                    var j = i + 1
                    while (j < n && style.headingLevel(lines[j].size) == level && !lines[j].mono) {
                        val gap = lines[j].bbox.y0 - group.last().bbox.y1
                        if (gap > 0.8 * ln.size) break
                        group.add(lines[j])
                        j += 1
                    }
                    val body = renderSpans(joinLineSpans(group, style.vocab), plain = true).trim()
                    if (body.length <= MAX_HEADING_CHARS && group.size <= 3) {
                        frags.add(Frag("heading", body, bbox = Box.union(group.map { it.bbox }), level = level))
                    } else {
                        // Too long for a heading (an author list, a large-print intro): prose.
                        frags.add(Frag("para", paragraph(group, left, right)))
                    }
                    i = j
                    inList = false
                    continue
                }
                if (ln.mono) {
                    val group = mutableListOf(ln)
                    var j = i + 1
                    while (j < n && lines[j].mono) {
                        group.add(lines[j]); j += 1
                    }
                    frags.add(Frag("code", code(group)))
                    i = j
                    inList = false
                    continue
                }
                val marker = MdText.splitListMarker(text)
                val prevOk = i == 0 || inList || MdText.endsSentence(lines[i - 1].text)
                if (marker != null && (marker.kind == "glyph" || prevOk)) {
                    val drop = (ln.text.length - ln.text.trimStart().length) + (text.length - marker.rest.length)
                    val itemLines = mutableListOf(Line(dropChars(ln.spans, drop), ln.bbox))
                    val textX0 = itemLines[0].spans.firstOrNull()?.bbox?.x0 ?: ln.x0
                    var j = i + 1
                    while (j < n) {
                        val nxt = lines[j]
                        val t = nxt.text.trim()
                        if (MdText.splitListMarker(t) != null || nxt.mono || style.headingLevel(nxt.size) != level) break
                        val gap = nxt.bbox.y0 - itemLines.last().bbox.y1
                        if (gap > 0.8 * maxOf(nxt.size, 1.0)) break
                        val indented = nxt.x0 >= textX0 - 2
                        if (!indented && MdText.endsSentence(itemLines.last().text)) break
                        itemLines.add(nxt)
                        j += 1
                    }
                    val body = renderSpans(joinLineSpans(itemLines, style.vocab)).trim()
                    frags.add(
                        Frag(
                            "list", MdText.escapeLineStart(body), x0 = ln.x0, marker = marker.marker,
                            bbox = Box.union(listOf(ln.bbox) + itemLines.drop(1).map { it.bbox }),
                        ),
                    )
                    i = j
                    inList = true
                    continue
                }
                // paragraph
                val group = mutableListOf(ln)
                var j = i + 1
                while (j < n) {
                    val nxt = lines[j]
                    val t = nxt.text.trim()
                    if (nxt.mono || style.headingLevel(nxt.size) != level) break
                    val m = MdText.splitListMarker(t)
                    if (m != null && (m.kind == "glyph" || MdText.endsSentence(group.last().text))) break
                    if (paragraphBreak(group.last(), nxt, left, right)) break
                    group.add(nxt)
                    j += 1
                }
                val para = paragraph(group, left, right)
                if (group.size == 1 && level == 0 && ln.size >= 0.95 * style.bodySize && isBoldTitle(ln)) {
                    // A lone bold line at body size ("3.1 Traces") may be a sub-heading;
                    // assemble() decides once it can see what follows.
                    val sub = minOf(style.headingSizes.size + 1, 6)
                    val body = renderSpans(joinLineSpans(group, style.vocab), plain = true).trim()
                    frags.add(Frag("title?", body, bbox = ln.bbox, level = sub, alt = para))
                } else {
                    frags.add(Frag("para", para, bbox = Box.union(group.map { it.bbox })))
                }
                i = j
                inList = false
            }
            return frags
        }

        private fun paragraphBreak(prev: Line, nxt: Line, left: Double, right: Double): Boolean {
            val size = maxOf(prev.size, nxt.size, 1.0)
            val gap = nxt.bbox.y0 - prev.bbox.y1
            if (gap > 0.7 * size) return true
            val width = maxOf(right - left, 1.0)
            val indent = nxt.x0 - left
            val prevEnded = MdText.endsSentence(prev.text)
            if (indent > 0.8 * size && prev.x0 - left < 0.3 * size && prevEnded && width > 20 * size) return true
            if (prevEnded && (right - prev.bbox.x1) > 0.3 * width && width > 20 * size) return true
            return false
        }

        private fun paragraph(lines: List<Line>, left: Double, right: Double): String {
            val width = maxOf(right - left, 1.0)
            if (lines.size >= 3) {
                val inner = lines.dropLast(1)
                val short = inner.count { (right - it.bbox.x1) > 0.2 * width }
                if (short >= 2 && short.toDouble() / inner.size >= 0.5) {
                    // Ragged lines (verse, addresses): keep the line breaks.
                    return lines.joinToString("\n") {
                        MdText.escapeLineStart(renderSpans(joinLineSpans(listOf(it), style.vocab)).trim())
                    }
                }
            }
            val body = renderSpans(joinLineSpans(lines, style.vocab)).trim()
            return MdText.escapeLineStart(body)
        }

        private fun code(lines: List<Line>): String {
            val widths = mutableListOf<Double>()
            for (l in lines) for (s in l.spans) if (s.text.length >= 2) widths.add((s.bbox.x1 - s.bbox.x0) / s.text.length)
            val cw = if (widths.isNotEmpty()) PyMath.median(widths) else 6.0
            val base = lines.minOf { it.x0 }
            val out = mutableListOf<String>()
            var prev: Line? = null
            for (l in lines) {
                if (prev != null) {
                    val gap = l.bbox.y0 - prev.bbox.y1
                    val blanks = (gap / maxOf(prev.bbox.y1 - prev.bbox.y0, 1.0) + 0.25).toInt()
                    repeat(minOf(blanks, 3)) { out.add("") }
                }
                val indent = if (cw > 0) Math.rint((l.x0 - base) / cw).toInt() else 0
                out.add(" ".repeat(maxOf(indent, 0)) + l.text.trimEnd())
                prev = l
            }
            val body = out.joinToString("\n")
            val fence = MdText.fenceFor(body)
            return "$fence\n$body\n$fence"
        }

        fun assemble(input: List<Frag>): String {
            val blocks = mutableListOf<String>()
            val listLines = mutableListOf<String>()
            val stack = mutableListOf<Pair<Double, String>>() // (x0, child indent)

            fun flush() {
                if (listLines.isNotEmpty()) {
                    blocks.add(listLines.joinToString("\n"))
                    listLines.clear()
                }
                stack.clear()
            }

            val frags = input.map { it.copy() }
            for ((i, f) in frags.withIndex()) {
                if (f.kind == "title?") {
                    val nxt = frags.getOrNull(i + 1)
                    f.kind = if (introduces(f, nxt)) "heading" else "para"
                    if (f.kind == "para") f.text = f.alt
                }
            }
            val merged = mutableListOf<Frag>()
            for (f in frags) {
                val p = merged.lastOrNull()
                val fb = f.bbox
                val pb = p?.bbox
                if (p != null && f.kind == "heading" && p.kind == "heading" && f.level == p.level &&
                    fb != null && pb != null &&
                    fb.y0 - pb.y1 >= 0 && fb.y0 - pb.y1 < 0.6 * (pb.y1 - pb.y0) &&
                    fb.x0 < pb.x1 && pb.x0 < fb.x1
                ) {
                    p.text = MdText.joinTwo(p.text, f.text, style.vocab)
                    p.bbox = Box.union(listOf(pb, fb))
                    continue
                }
                merged.add(f.copy())
            }
            for (f in merged) {
                if (f.kind == "heading") {
                    if (f.text.length > MAX_HEADING_CHARS) {
                        f.kind = "para" // several heading-sized blocks that together are prose
                        f.text = MdText.escapeLineStart(f.text)
                    } else {
                        f.text = "#".repeat(f.level) + " " + f.text
                    }
                }
            }

            for (f in merged) {
                if (f.kind == "list") {
                    while (stack.isNotEmpty() && f.x0 < stack.last().first - 2) stack.removeAt(stack.size - 1)
                    if (stack.isNotEmpty() && Math.abs(f.x0 - stack.last().first) <= 2) stack.removeAt(stack.size - 1)
                    val indent = stack.lastOrNull()?.second ?: ""
                    listLines.add("$indent${f.marker} ${f.text}".trimEnd())
                    val child = f.marker.split(" ")[0].length + 1
                    stack.add(f.x0 to indent + " ".repeat(child))
                    continue
                }
                flush()
                blocks.add(f.text)
            }
            flush()
            return MdText.mergeContinuations(blocks.filter { it.isNotBlank() }, style.vocab).joinToString("\n\n")
        }
    }

    // ---------------------------------------------------------- top level

    fun inside(inner: Box, outer: Box, frac: Double = 0.6): Boolean {
        val ix0 = maxOf(inner.x0, outer.x0)
        val iy0 = maxOf(inner.y0, outer.y0)
        val ix1 = minOf(inner.x1, outer.x1)
        val iy1 = minOf(inner.y1, outer.y1)
        if (ix1 <= ix0 || iy1 <= iy0) return false
        val area = maxOf((inner.x1 - inner.x0) * (inner.y1 - inner.y0), 1e-6)
        return (ix1 - ix0) * (iy1 - iy0) / area >= frac
    }

    /** A ready-made block on the page (a table's or an image's Markdown). */
    class Placed(val bbox: Box, val markdown: String)

    /**
     * Markdown for one digital page. [tables] and [images] are optional extras
     * found by the PDF reader; [figures] are areas whose labels are pictured.
     */
    fun pageMarkdown(
        data: PageData,
        style: DocStyle,
        tables: List<Placed> = emptyList(),
        figures: List<Box> = emptyList(),
        images: List<Placed> = emptyList(),
    ): String {
        val renderer = PageRenderer(style)
        val items = mutableListOf<Item>()
        for (block in mergeBullets(data.blocks)) {
            val kept = block.filter { ln ->
                if (tables.any { inside(ln.bbox, it.bbox, 0.5) }) return@filter false
                // labels drawn inside a figure are in its picture
                !(figures.any { inside(ln.bbox, it, 0.9) } && ln.text.trim().length <= 40)
            }
            if (kept.isNotEmpty()) items.add(Item("text", Box.union(kept.map { it.bbox }), lines = kept))
        }
        for (t in tables) items.add(Item("table", t.bbox, markdown = t.markdown))
        for (im in images) items.add(Item("image", im.bbox, markdown = im.markdown))

        var ordered = Layout.readingOrder(items, { it.bbox }, { maxOf(it.lines.size, 1) })
        // Footnotes (small print low on the page) go last, so a paragraph they
        // interrupt at a column break can be joined back together.
        val notes = ordered.filter { isFootnote(it, style, data.height) }
        if (notes.isNotEmpty()) ordered = ordered.filter { it !in notes } + notes
        val frags = mutableListOf<Frag>()
        for (it in ordered) {
            if (it.kind == "text") frags.addAll(renderer.blockFragments(it.lines))
            else frags.add(Frag(it.kind, it.markdown))
        }
        return renderer.assemble(frags)
    }

    private fun isFootnote(item: Item, style: DocStyle, pageHeight: Double): Boolean {
        if (item.kind != "text" || item.bbox.y0 < 0.55 * pageHeight) return false
        if (item.lines.any { it.size > style.bodySize * 0.9 }) return false
        return !MdText.isCaption(item.lines[0].text)
    }

    /** Attach a bullet glyph that sits alone (often its own block) to its text. */
    fun mergeBullets(blocks: List<List<Line>>): List<List<Line>> {
        data class Ref(val bi: Int, val li: Int, val line: Line)
        val all = blocks.flatMapIndexed { bi, b -> b.mapIndexed { li, l -> Ref(bi, li, l) } }
        val remove = HashSet<Pair<Int, Int>>()
        for ((bi, li, l) in all) {
            if (!MdText.isBulletGlyph(l.text)) continue
            val cy = (l.bbox.y0 + l.bbox.y1) / 2
            var best: Pair<Double, Line>? = null
            for ((bj, lj, m) in all) {
                if ((bj == bi && lj == li) || (bj to lj) in remove || MdText.isBulletGlyph(m.text)) continue
                val h = maxOf(m.bbox.y1 - m.bbox.y0, 1.0)
                val mcy = (m.bbox.y0 + m.bbox.y1) / 2
                val dx = m.bbox.x0 - l.bbox.x1
                if (Math.abs(mcy - cy) <= 0.5 * h && dx >= -1 && dx <= 40) {
                    if (best == null || dx < best.first) best = dx to m
                }
            }
            if (best != null) {
                val m = best.second
                var glyph = l.text.trim()
                if (MdText.isPrivateUse(glyph[0])) glyph = "•"
                m.spans.add(0, Span("$glyph ", l.bbox, l.spans[0].size))
                m.bbox = Box.union(listOf(m.bbox, l.bbox))
                remove.add(bi to li)
            }
        }
        return blocks.mapIndexedNotNull { bi, b ->
            b.filterIndexed { li, _ -> (bi to li) !in remove }.takeIf { it.isNotEmpty() }
        }
    }
}
