package io.github.penpaper0878.pdf2md.core

/** Tidy what a vision model returns, and notice when it has gone wrong (port of ocr/cleanup.py). */
object OcrCleanup {
    private val THINK_RE = Regex("<think>.*?</think>", setOf(RegexOption.DOT_MATCHES_ALL, RegexOption.IGNORE_CASE))
    private val OPEN_FENCE_RE = Regex("""^\s*```[ \t]*(markdown|md|text)?[ \t]*\n""", RegexOption.IGNORE_CASE)
    private val CLOSE_FENCE_RE = Regex("""\n```\s*$""")
    private val PREAMBLE_RE = Regex(
        """^(sure|certainly|of course|okay|ok|here is|here's|below is|the following is|this is)\b[^\n]{0,160}""" +
            """(transcri|markdown|text|content|page|image)[^\n]*[:.]?\s*$""",
        RegexOption.IGNORE_CASE,
    )
    private val POSTAMBLE_RE = Regex(
        """^(let me know|i hope|feel free|if you (need|want|would)|note:|please note)\b[^\n]*$""",
        RegexOption.IGNORE_CASE,
    )
    private val BLANK_MARKERS = setOf("[blank]", "blank", "[blank page]", "(blank)", "[empty]", "[no text]", "no text")
    private val MANY_NEWLINES = Regex("\n{3,}")
    private val TAIL_REPEAT_RE = Regex("""(.{2,200}?)(?:\s*\1){7,}\s*$""", RegexOption.DOT_MATCHES_ALL)
    private val WORD_CHAR = Regex("""[\p{L}\p{N}_]""")
    private val RULE_LINE = Regex("""[|\-:\s]*""")

    fun cleanModelOutput(raw: String?): String {
        var text = (raw ?: "").replace("\r\n", "\n").replace("\r", "\n")
        text = THINK_RE.replace(text) { "" }.trim()
        var lines = text.split("\n")
        if (lines.isNotEmpty() && PREAMBLE_RE.find(lines[0].trim()) != null) lines = lines.drop(1)
        val kept = lines.toMutableList()
        while (kept.isNotEmpty() && (kept.last().isBlank() || POSTAMBLE_RE.find(kept.last().trim()) != null)) {
            kept.removeAt(kept.size - 1)
        }
        text = kept.joinToString("\n") { it.trimEnd() }.trim()
        // The whole answer wrapped in a code fence.
        val m = OPEN_FENCE_RE.find(text)
        if (m != null && CLOSE_FENCE_RE.find(text) != null && countOf(text, "```") == 2) {
            text = CLOSE_FENCE_RE.replace(text.substring(m.range.last + 1)) { "" }.trim()
        } else if (m != null && !text.substring(m.range.last + 1).contains("```")) {
            text = text.substring(m.range.last + 1).trim() // opened, never closed (cut off)
        }
        if (text.lowercase() in BLANK_MARKERS) return ""
        text = MANY_NEWLINES.replace(text) { "\n\n" }
        return if (text.isNotEmpty()) text + "\n" else ""
    }

    private fun countOf(text: String, needle: String): Int {
        var n = 0
        var i = text.indexOf(needle)
        while (i >= 0) {
            n += 1
            i = text.indexOf(needle, i + needle.length)
        }
        return n
    }

    /** A phrase repeated 8+ times at the very end, if it is made of words (not dots or pipes). */
    private fun tailRepeat(text: String): MatchResult? {
        val m = TAIL_REPEAT_RE.find(text) ?: return null
        return if (WORD_CHAR.findAll(m.groupValues[1]).count() >= 2) m else null
    }

    /** True if the model got stuck repeating itself (a common failure on hard pages). */
    fun looksDegenerate(text: String): Boolean {
        var run = 0
        var prev: String? = null
        for (raw in text.split("\n")) {
            val l = raw.trim()
            if (l.isEmpty()) continue
            if (l == prev && !RULE_LINE.matches(l)) {
                run += 1
                if (run >= 5) return true
            } else {
                run = 0
                prev = l
            }
        }
        return tailRepeat(text.takeLast(4000)) != null
    }

    /** Keep one copy of a looping line or phrase (only used on output already judged degenerate). */
    fun collapseRepetition(input: String): String {
        val out = mutableListOf<String>()
        var prev: String? = null
        for (line in input.split("\n")) {
            val key = line.trim()
            if (key.isNotEmpty() && key == prev && !RULE_LINE.matches(key)) continue
            out.add(line)
            if (key.isNotEmpty()) prev = key
        }
        var text = out.joinToString("\n")
        val tail = text.takeLast(4000)
        val m = tailRepeat(tail)
        if (m != null) text = text.substring(0, text.length - tail.length + m.range.first) + m.groupValues[1]
        return text.trimEnd() + "\n"
    }
}

/** The transcription prompt, identical to the desktop tool's (ocr/vlm.py). */
object Prompt {
    const val VERSION = "3"

    val LANG_NAMES = mapOf(
        "eng" to "English", "hin" to "Hindi", "guj" to "Gujarati", "mar" to "Marathi", "ben" to "Bengali",
        "tam" to "Tamil", "tel" to "Telugu", "kan" to "Kannada", "mal" to "Malayalam", "pan" to "Punjabi",
        "ori" to "Odia", "asm" to "Assamese", "urd" to "Urdu", "san" to "Sanskrit", "nep" to "Nepali",
        "fra" to "French", "deu" to "German", "spa" to "Spanish", "ita" to "Italian", "por" to "Portuguese",
        "nld" to "Dutch", "rus" to "Russian", "ukr" to "Ukrainian", "pol" to "Polish", "tur" to "Turkish",
        "ara" to "Arabic", "fas" to "Persian", "heb" to "Hebrew", "ell" to "Greek",
        "chi_sim" to "Chinese (Simplified)", "chi_tra" to "Chinese (Traditional)", "jpn" to "Japanese",
        "kor" to "Korean", "tha" to "Thai", "vie" to "Vietnamese", "ind" to "Indonesian", "msa" to "Malay",
    )

    private val TEMPLATE = listOf(
        "Transcribe this {what} into Markdown.",
        "",
        "Rules:",
        "- Copy the text exactly as written: the same words, spelling, numbers, punctuation and capitalisation. Do not correct mistakes, translate, summarise, explain or add anything.",
        "- Keep every script as it is (Latin, Devanagari, Gujarati, Arabic, …). Never transliterate.",
        "- Handwriting: read carefully and use the surrounding words as context. If a word cannot be read at all, write [illegible]. If you are unsure of a word, write your best reading followed by [?].",
        "- Structure: use Markdown headings only for text that is visibly a title or heading (larger, underlined, boxed or set apart). Keep bullet and numbered lists as Markdown lists, with their nesting. Keep paragraph breaks. Use a Markdown table for anything laid out as a table.",
        "- Use **bold** or *italic* only where the page clearly shows it. Crossed-out text: ~~text~~. Checkboxes: - [ ] and - [x].",
        "- Maths: LaTeX, \$...\$ inline and \$\$...\$\$ on a line of its own. Chemical formulas, units and symbols exactly as written.",
        "- Drawings, diagrams, charts and photos: do not describe their shapes; write one line \"[Figure: short description]\" where they appear, followed by any words written in them.",
        "- Leave out page numbers, ruled lines, margins, punch holes, stains and scanner marks.",
        "- Follow the natural reading order: top to bottom, and for side-by-side columns finish the left column before the right.",
        "- If there is no text at all, output only: [blank]",
        "{extra}",
        "Output only the Markdown, with no introduction, no comments and no code fence around it.",
    ).joinToString("\n")

    fun languageNames(lang: String?): List<String> {
        if (lang.isNullOrEmpty()) return emptyList()
        return lang.replace(",", "+").split("+").filter { it.isNotEmpty() && it != "osd" }.map { LANG_NAMES[it] ?: it }
    }

    fun build(part: Int = 1, parts: Int = 1, lang: String? = null, hint: String? = null): String {
        val extra = mutableListOf<String>()
        if (parts > 1) {
            extra.add(
                "- This image is part $part of $parts of one page, cut through a blank gap. " +
                    "It may begin or end in the middle of a sentence, list or table; transcribe only what is visible.",
            )
        }
        val names = languageNames(lang)
        if (names.isNotEmpty()) {
            val joined = if (names.size > 1) names.dropLast(1).joinToString(", ") + " and " + names.last() else names[0]
            extra.add("- The text is written in $joined.")
        }
        if (!hint.isNullOrEmpty()) extra.add("- About the document (use only to help read unclear words): ${hint.trim()}")
        val what = if (parts == 1) "page" else "part of a page"
        return TEMPLATE.replace("{what}", what).replace("{extra}", extra.joinToString("\n"))
    }
}

/** A recognised word with its box, as OCR engines (Tesseract, ML Kit) report them. */
data class OcrWord(
    val text: String,
    val left: Int,
    val top: Int,
    val width: Int,
    val height: Int,
    val conf: Double = 95.0,
    val block: Int = 0,
    val par: Int = 0,
    val line: Int = 0,
)

/** Structured Markdown from OCR'd words: paragraphs, headings, lists (port of tesseract.py's layout). */
object OcrLayout {
    private class OLine(val block: Int, var x0: Int, var top: Int) {
        val words = mutableListOf<OcrWord>()
        var x1 = 0
        var bottom = 0
        var text = ""
        var size = 0.0
    }

    private fun lines(words: List<OcrWord>): List<OLine> {
        val map = LinkedHashMap<Triple<Int, Int, Int>, OLine>()
        for (w in words) {
            val key = Triple(w.block, w.par, w.line)
            val ln = map.getOrPut(key) { OLine(w.block, w.left, w.top) }
            ln.words.add(w)
            ln.x0 = minOf(ln.x0, w.left)
            ln.x1 = maxOf(ln.x1, w.left + w.width)
            ln.top = minOf(ln.top, w.top)
            ln.bottom = maxOf(ln.bottom, w.top + w.height)
        }
        val out = map.entries.sortedWith(compareBy({ it.key.first }, { it.key.second }, { it.key.third })).map { it.value }
        for (ln in out) {
            ln.text = MdText.normalizeText(ln.words.joinToString(" ") { it.text })
            ln.size = PyMath.median(ln.words.map { it.height.toDouble() })
        }
        return out
    }

    private fun paragraphs(lines: List<OLine>): List<List<OLine>> {
        val height = PyMath.median(lines.map { (it.bottom - it.top).toDouble() })
        val gaps = lines.zipWithNext().filter { (a, b) -> b.block == a.block && b.top > a.top }
            .map { (a, b) -> (b.top - a.bottom).toDouble() }
        val typical = if (gaps.isNotEmpty()) PyMath.median(gaps) else 0.3 * height
        val limit = maxOf(typical * 1.8, typical + 0.5 * height)
        val paras = mutableListOf<MutableList<OLine>>()
        for (ln in lines) {
            if (paras.isNotEmpty()) {
                val prev = paras.last().last()
                val gap = ln.top - prev.bottom
                val sizeJump = Math.abs(ln.size - prev.size) > 0.3 * maxOf(ln.size, prev.size)
                if (gap <= limit && ln.top > prev.top - 0.5 * height && !sizeJump) {
                    paras.last().add(ln)
                    continue
                }
            }
            paras.add(mutableListOf(ln))
        }
        return paras
    }

    private fun join(lines: List<OLine>, vocab: Set<String>): String {
        val left = lines.minOf { it.x0 }
        val right = lines.maxOf { it.x1 }
        val width = maxOf(right - left, 1)
        var out = lines[0].text.trim()
        for ((prev, cur) in lines.zipWithNext()) {
            val t = cur.text.trim()
            val short = right - prev.x1 > 0.15 * width
            val startsNew = t.isNotEmpty() && (t[0].isUpperCase() || t[0].isDigit())
            val prevEnd = prev.text.trimEnd()
            if (short && startsNew && !(prevEnd.endsWith("-") || prevEnd.endsWith(",") || prevEnd.endsWith("\u00ad"))) {
                out += "\n" + t
            } else {
                out = MdText.joinTwo(out, t, vocab)
            }
        }
        return out
    }

    private fun mdLines(text: String) = text.split("\n").joinToString("\n") { MdText.escapeLineStart(MdText.escapeInline(it)) }

    /** Markdown and the mean word confidence (null when nothing was read). */
    fun toMarkdown(allWords: List<OcrWord>): Pair<String, Double?> {
        var words = allWords.filter { it.conf >= 0 && it.text.isNotBlank() }
        if (words.isEmpty()) return "" to null
        // A "word" far taller and narrower than its letters allow is a misread
        // column of numbers, rotated margin text or a logo: noise, not content.
        val typical = PyMath.median(words.map { it.height.toDouble() })
        words = words.filter { w ->
            !(w.height > 2.5 * typical && w.width.toDouble() / maxOf(w.height, 1) < 0.3 * w.text.trim().length)
        }
        if (words.isEmpty()) return "" to null
        // Lines of one to three stray symbols ("|", "~ .") are specks and rules.
        val lines = lines(words).filter { l -> l.text.any { it.isLetterOrDigit() } || l.text.trim().length > 3 }
        if (lines.isEmpty()) return "" to null
        val body = PyMath.median(lines.map { it.size })
        val vocab = MdText.buildVocab(lines.map { it.text })
        val blocks = mutableListOf<String>()
        val listRun = mutableListOf<String>()
        fun flush() {
            if (listRun.isNotEmpty()) {
                blocks.add(listRun.joinToString("\n"))
                listRun.clear()
            }
        }
        for (plines0 in paragraphs(lines)) {
            var plines = plines0
            val size = PyMath.median(plines.map { it.size })
            val textAll = plines.joinToString(" ") { it.text }
            if (plines.size <= 2 && size >= 1.45 * body && textAll.length <= 120) {
                flush()
                val level = if (size >= 2.2 * body) 1 else 2
                blocks.add("#".repeat(level) + " " + MdText.escapeInline(MdText.joinLines(plines.map { it.text }, vocab)))
                continue
            }
            // A paragraph can hold several list items.
            val items = mutableListOf<MutableList<OLine>>()
            for (l in plines) {
                if (MdText.splitListMarker(l.text) != null || items.isEmpty()) items.add(mutableListOf(l))
                else items.last().add(l)
            }
            val firstMarker = MdText.splitListMarker(items[0][0].text)
            if (firstMarker != null && (firstMarker.kind != "labelled" || items.size >= 2)) {
                for (item in items) {
                    val m = MdText.splitListMarker(item[0].text)!!
                    val rest = listOf(m.rest) + item.drop(1).map { it.text }
                    listRun.add(m.marker + " " + MdText.escapeLineStart(MdText.escapeInline(MdText.joinLines(rest, vocab))))
                }
                continue
            }
            flush()
            val first = plines[0]
            val width = plines.maxOf { it.x1 } - plines.minOf { it.x0 }
            val firstEnd = first.text.trimEnd().takeLast(1)
            if (plines.size >= 2 && first.x1 - first.x0 < 0.6 * width && first.words.size <= 8 &&
                !MdText.endsSentence(first.text) && firstEnd != "," && firstEnd != "-"
            ) {
                // A short title line ("Introduction") sitting on its paragraph.
                blocks.add(mdLines(first.text.trim()))
                plines = plines.drop(1)
            }
            blocks.add(mdLines(join(plines, vocab)))
        }
        flush()
        val confidence = words.map { it.conf }.average()
        val md = blocks.filter { it.isNotBlank() }.joinToString("\n\n")
        return (if (md.isNotEmpty()) md + "\n" else "") to confidence
    }
}

/** Running headers/footers that OCR transcribed on page after page (port of converter.strip_repeated_edges). */
object EdgeStrip {
    class OcrPage(var markdown: String, val printedOcr: Boolean)

    private val EDGE_MARKUP_RE = Regex("""^[#>*_\s\p{Z}-]+|[*_|\s\p{Z}]+$""")
    private val PAGE_NUMBER_RE = Regex("""^(page|p\.|pg\.?)?[\s\p{Z}]*\p{Nd}{1,4}([\s\p{Z}]*(of|/)[\s\p{Z}]*\p{Nd}{1,4})?$""", RegexOption.IGNORE_CASE)
    private val WS = Regex("""[\s\p{Z}]+""")

    private fun edgeKey(block: String): String? {
        val text = block.trim()
        if ('\n' in text || text.length > 160) return null
        return WS.replace(EDGE_MARKUP_RE.replace(text) { "" }) { " " }.lowercase().ifEmpty { null }
    }

    fun strip(pages: List<OcrPage>) {
        if (pages.size < 4) return
        val counts = HashMap<String, Int>()
        for (p in pages) {
            val blocks = MdText.splitBlocks(p.markdown)
            val keys = (blocks.take(3) + blocks.takeLast(3)).mapNotNull { edgeKey(it) }.toSet()
            for (k in keys) counts[k] = (counts[k] ?: 0) + 1
        }
        val need = maxOf(3, (pages.size + 1) / 2)
        val repeated = counts.filterValues { it >= need }.keys

        for (p in pages) {
            val blocks = MdText.splitBlocks(p.markdown)
            val n = blocks.size
            val edge = (0 until minOf(3, n)).toSet() + (maxOf(0, n - 3) until n).toSet()
            val kept = blocks.filterIndexed { i, b ->
                if (i !in edge) return@filterIndexed true
                val key = edgeKey(b) ?: return@filterIndexed true
                !(key in repeated || (p.printedOcr && PAGE_NUMBER_RE.find(key) != null))
            }
            p.markdown = if (kept.isNotEmpty()) kept.joinToString("\n\n") + "\n" else ""
        }
    }
}

/**
 * Grey-level page images: where to cut a page into strips, and whether it is
 * blank (port of render.py). [gray] holds one byte per pixel, row by row.
 */
object PageImage {
    /** numpy.percentile (linear interpolation) for 8-bit values, via a histogram. */
    fun percentile(gray: ByteArray, p: Double): Double {
        val hist = LongArray(256)
        for (b in gray) hist[b.toInt() and 0xff]++
        val n = gray.size
        val pos = p / 100.0 * (n - 1)
        val k = Math.floor(pos).toLong()
        val frac = pos - k
        fun valueAt(index: Long): Int {
            var seen = 0L
            for (v in 0..255) {
                seen += hist[v]
                if (seen > index) return v
            }
            return 255
        }
        val lo = valueAt(k)
        val hi = if (k + 1 < n) valueAt(k + 1) else lo
        return lo + frac * (hi - lo)
    }

    private fun rowInk(gray: ByteArray, width: Int, height: Int): DoubleArray {
        val background = percentile(gray, 90.0)
        val limit = minOf(background - 60, 170.0)
        val rows = DoubleArray(height)
        for (y in 0 until height) {
            var ink = 0
            val base = y * width
            for (x in 0 until width) if ((gray[base + x].toInt() and 0xff) < limit) ink++
            val r = ink.toDouble() / width
            // Printed rules on lined paper cross the whole page: not text.
            rows[y] = if (r > 0.6) 0.0 else r
        }
        return rows
    }

    /** Row positions that split the page into [parts] strips through blank space. */
    fun findCuts(gray: ByteArray, width: Int, height: Int, parts: Int): List<Int> {
        if (parts <= 1) return emptyList()
        val rows = rowInk(gray, width, height)
        val h = rows.size
        val blank = BooleanArray(h) { rows[it] <= 0.002 }
        val dist = DoubleArray(h)
        var run = 0
        for (i in 0 until h) {
            run = if (blank[i]) run + 1 else 0
            dist[i] = run.toDouble()
        }
        run = 0
        for (i in h - 1 downTo 0) {
            run = if (blank[i]) run + 1 else 0
            dist[i] = minOf(dist[i], run.toDouble())
        }
        val k = maxOf(3, h / 200)
        // numpy.convolve(rows, ones(k)/k, mode="same")
        val c = (k - 1) / 2
        val smooth = DoubleArray(h) { i ->
            var s = 0.0
            for (m in (i - k + 1 + c)..(i + c)) if (m in 0 until h) s += rows[m] / k
            s
        }
        val cuts = mutableListOf<Int>()
        val halfWindow = (h.toDouble() / parts * 0.35).toInt()
        for (n in 1 until parts) {
            val target = (h.toDouble() * n / parts).toInt()
            val lo = maxOf(1, target - halfWindow)
            val hi = minOf(h - 1, target + halfWindow)
            var cut: Int
            val windowMax = if (hi > lo) (lo until hi).maxOf { dist[it] } else 0.0
            if (hi > lo && windowMax > 0) {
                // Among rows in the widest gaps, take the one closest to the target.
                val candidates = (lo until hi).filter { dist[it] >= windowMax * 0.8 }
                cut = candidates.minByOrNull { Math.abs(it - target) }!!
            } else {
                cut = lo
                for (i in lo until hi) if (smooth[i] < smooth[cut]) cut = i
            }
            if (cuts.isEmpty() || cut - cuts.last() > h * 0.1) cuts.add(cut)
        }
        return cuts
    }

    /** True for a page with no marks at all (faint pencil counts; one word is enough). */
    fun isBlank(gray: ByteArray, threshold: Double = 0.00002): Boolean {
        if (gray.isEmpty()) return true
        val limit = percentile(gray, 95.0) - 40
        var marks = 0L
        for (b in gray) if ((b.toInt() and 0xff) < limit) marks++
        return marks.toDouble() / gray.size < threshold
    }
}

/** Measurements of a page, made by the PDF reader, that decide how it is read. */
data class PageStats(
    val number: Int,
    val visibleChars: Int,
    val invisibleChars: Int,
    val garbageChars: Int,
    val imageCoverage: Double,
    val largestImage: Double,
    val inkStrokes: Int,
    val inkCoverage: Double,
    val inkAnnotations: Int,
    val drawings: Int,
)

/** digital | scanned | mixed | empty, with a reason (port of classify.py's decision). */
data class PageKind(val kind: String, val reason: String)

object Classify {
    const val MIN_TEXT_CHARS = 20
    const val MAX_GARBAGE_RATIO = 0.3
    const val MIN_INK_STROKES = 15
    const val MIN_INK_COVERAGE = 0.03

    fun decide(s: PageStats): PageKind {
        val garbageRatio = if (s.visibleChars > 0) s.garbageChars.toDouble() / s.visibleChars else 0.0
        val hasInk = s.inkAnnotations > 0 || (s.inkStrokes >= MIN_INK_STROKES && s.inkCoverage >= MIN_INK_COVERAGE)
        val readable = s.visibleChars - s.garbageChars
        return when {
            s.visibleChars >= MIN_TEXT_CHARS && garbageRatio >= MAX_GARBAGE_RATIO ->
                PageKind("scanned", "text layer uses an unreadable font encoding")
            readable < MIN_TEXT_CHARS -> when {
                s.invisibleChars >= MIN_TEXT_CHARS && s.imageCoverage > 0.05 -> PageKind("scanned", "scanned image with a hidden OCR layer")
                s.imageCoverage > 0.02 -> PageKind("scanned", "page is an image")
                hasInk || s.inkStrokes >= 3 -> PageKind("scanned", "handwriting stored as vector ink")
                s.drawings >= 20 -> PageKind("scanned", "text drawn as outlines")
                s.visibleChars > 0 -> PageKind("digital", "short text")
                else -> PageKind("empty", "blank page")
            }
            hasInk -> PageKind("mixed", "typed text with handwriting")
            else -> PageKind("digital", "text layer")
        }
    }

    /** Characters that mean the font has no usable Unicode mapping. */
    fun isGarbage(cp: Int): Boolean =
        cp == 0xFFFD || cp < 32 || cp in 0xE000..0xF8FF || cp in 0xFFF0..0xFFFF
}
