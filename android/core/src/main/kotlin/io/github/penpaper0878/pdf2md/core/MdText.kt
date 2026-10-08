package io.github.penpaper0878.pdf2md.core

import java.text.Normalizer

/**
 * Markdown text helpers: escaping, list markers, line joining, block merging.
 *
 * A line-for-line port of the desktop tool's `mdtext.py`, so both produce the
 * same Markdown. Python's Unicode-aware `\w \d \s` are spelled out with
 * `\p{..}` classes, which mean the same on the JVM and on Android's ICU regex
 * engine (where the `(?U)` flag is not supported).
 */
object MdText {

    private val LIGATURES = mapOf(
        "ﬀ" to "ff", "ﬁ" to "fi", "ﬂ" to "fl", "ﬃ" to "ffi",
        "ﬄ" to "ffl", "ﬅ" to "st", "ﬆ" to "st",
    )
    private val LIGATURE_RE = Regex("[ﬀ-ﬆ]")
    private val SPACES_RE = Regex("[\u00a0\u2000-\u200a\u2028\u2029\u202f\u205f\u3000]")
    private val ZERO_WIDTH_RE = Regex("[\u200b\u2060\ufeff]")

    // TeX-made PDFs often draw an accent as a separate spacing character before
    // its letter ("na¨ıve" for "naïve"). Map each to its combining form.
    private val SPACING_ACCENTS = mapOf(
        '¨' to '\u0308', '´' to '\u0301', 'ˋ' to '\u0300', 'ˆ' to '\u0302',
        '˜' to '\u0303', '¸' to '\u0327', 'ˇ' to '\u030c', '˘' to '\u0306',
        '˚' to '\u030a', '¯' to '\u0304', '˙' to '\u0307', '˝' to '\u030b',
    )
    private val ACCENT_RE = Regex("([" + SPACING_ACCENTS.keys.joinToString("") + "]) ?([A-Za-zıȷ])")

    /** Expand ligatures, rebuild split accents and tidy invisible characters. Never NFKC. */
    fun normalizeText(text: String): String {
        var t = LIGATURE_RE.replace(text) { LIGATURES.getValue(it.value) }
        t = SPACES_RE.replace(t) { " " }
        t = ZERO_WIDTH_RE.replace(t) { "" }
        if (t.any { it in SPACING_ACCENTS }) {
            t = ACCENT_RE.replace(t) { m ->
                val base = when (val b = m.groupValues[2]) {
                    "ı" -> "i"
                    "ȷ" -> "j"
                    else -> b
                }
                val combined = Normalizer.normalize(
                    base + SPACING_ACCENTS.getValue(m.groupValues[1][0]), Normalizer.Form.NFC,
                )
                // Only when it makes a real letter: "don´t" is an apostrophe, not a "ť".
                if (combined.length == 1) combined else m.value
            }
        }
        return t
    }

    // ------------------------------------------------------------ escaping

    private val INLINE_ESCAPE_RE = Regex("""([\\`*])""")
    // "_" only starts emphasis next to a non-word character.
    private val UNDERSCORE_RE = Regex("""(?<![0-9A-Za-z])_|_(?![0-9A-Za-z])""")
    private val HTML_TAG_RE = Regex("""<(?=[A-Za-z/!?])""")
    private val LINK_TEXT_RE = Regex("""\[(?=[^\]]*\]\()""")

    /** Escape characters that Markdown would otherwise treat as formatting. */
    fun escapeInline(text: String): String {
        var t = INLINE_ESCAPE_RE.replace(text) { "\\" + it.value }
        t = UNDERSCORE_RE.replace(t) { "\\_" }
        t = HTML_TAG_RE.replace(t) { "\\<" }
        t = LINK_TEXT_RE.replace(t) { "\\[" }
        return t
    }

    private val LINE_START: List<Pair<Regex, (MatchResult) -> String>> = listOf(
        Regex("""^(#{1,6})(?=[\s\p{Z}]|$)""") to { m -> "\\" + m.groupValues[1] },
        Regex("""^>""") to { _ -> "\\>" },
        Regex("""^([-+])(?=[\s\p{Z}])""") to { m -> "\\" + m.groupValues[1] },
        Regex("""^(\p{Nd}{1,9})([.)])(?=[\s\p{Z}]|$)""") to { m -> m.groupValues[1] + "\\" + m.groupValues[2] },
        Regex("""^(=+|-+)[\s\p{Z}]*$""") to { m -> "\\" + m.value },
        Regex("""^(```|~~~)""") to { m -> "\\" + m.groupValues[1] },
        Regex("""^\|""") to { _ -> "\\|" },
    )

    /** Escape a line's first characters if they would start a Markdown block. */
    fun escapeLineStart(line: String): String {
        for ((pattern, repl) in LINE_START) {
            val m = pattern.find(line) ?: continue
            return line.substring(0, m.range.first) + repl(m) + line.substring(m.range.last + 1)
        }
        return line
    }

    fun escapeTableCell(text: String): String {
        val collapsed = text.trim().split(WS_RUN).filter { it.isNotEmpty() }.joinToString(" ")
        return escapeInline(collapsed).replace("|", "\\|")
    }

    private val WS_RUN = Regex("""[\s\p{Z}]+""")

    /** Wrap text in a backtick code span long enough not to clash with it. */
    fun codeSpan(text: String): String {
        val longest = Regex("`+").findAll(text).maxOfOrNull { it.value.length } ?: 0
        val fence = "`".repeat(longest + 1)
        val pad = if (text.startsWith("`") || text.endsWith("`")) " " else ""
        return fence + pad + text + pad + fence
    }

    fun fenceFor(text: String): String {
        val longest = Regex("^`{3,}", RegexOption.MULTILINE).findAll(text).maxOfOrNull { it.value.length } ?: 0
        return "`".repeat(maxOf(3, longest + 1))
    }

    // -------------------------------------------------------- list markers

    val BULLET_CHARS: Set<Char> = "•◦▪▫‣⁃●○■□►▸▹▶➢➤➣✓✔✗✘❖◆◇◉⦿∙·*-–".toSet()
    val CHECK_OPEN: Set<Char> = "☐❑❒".toSet()
    val CHECK_DONE: Set<Char> = "☑☒✅".toSet()

    private val ORDERED_RE = Regex("""^(\p{Nd}{1,3})([.)])[\s\p{Z}]+(?=[^\s\p{Z}])""")
    private val LABEL_RE = Regex("""^(\((?:\p{Nd}{1,3}|[a-z]|[ivxlc]{1,5})\)|(?:[a-z]|[ivxlc]{1,5})[.)])[\s\p{Z}]+(?=[^\s\p{Z}])""")

    fun isPrivateUse(ch: Char): Boolean = ch in '\ue000'..'\uf8ff'

    /** True if text is only a bullet glyph (as symbol fonts often emit it). */
    fun isBulletGlyph(text: String): Boolean {
        val t = text.trim()
        if (t.length != 1) return false
        val c = t[0]
        return c in BULLET_CHARS || c in CHECK_OPEN || c in CHECK_DONE || isPrivateUse(c)
    }

    /** kind is "glyph", "dash", "ordered" or "labelled" (see mdtext.split_list_marker). */
    data class ListMarker(val kind: String, val marker: String, val rest: String)

    fun splitListMarker(text: String): ListMarker? {
        val s = text.trimStart()
        if (s.isEmpty()) return null
        val first = s[0]
        val rest = s.substring(1)
        if (first in CHECK_OPEN || first in CHECK_DONE) {
            val box = if (first in CHECK_DONE) "[x]" else "[ ]"
            return ListMarker("glyph", "- $box", rest.trimStart())
        }
        if (first in BULLET_CHARS || isPrivateUse(first)) {
            if (rest.isBlank()) return null
            if (first in "-*–") {
                // Only with a space after them: "-5" is a number, "*emphasis*" is not a list.
                if (!rest[0].isWhitespace()) return null
                return ListMarker("dash", "-", rest.trimStart())
            }
            return ListMarker("glyph", "-", rest.trimStart())
        }
        ORDERED_RE.find(s)?.let { m ->
            return ListMarker("ordered", m.groupValues[1] + m.groupValues[2], s.substring(m.range.last + 1))
        }
        LABEL_RE.find(s)?.let { m ->
            return ListMarker("labelled", "- " + m.groupValues[1], s.substring(m.range.last + 1))
        }
        return null
    }

    // -------------------------------------------------------- line joining

    private const val SENTENCE_END = ".!?:;।॥。！？…"
    private const val CLOSERS = "\"'”’»)]}*_`"

    fun endsSentence(text: String): Boolean {
        val t = text.trimEnd().trimEnd { it in CLOSERS }
        return t.isEmpty() || t.last() in SENTENCE_END
    }

    private val WORD_RE = Regex("""[\p{L}\p{N}_]+(?:[-‐][\p{L}\p{N}_]+)*""")

    /** Lower-cased words (hyphenated compounds kept whole) seen in the text. */
    fun buildVocab(texts: Iterable<String>): MutableSet<String> {
        val vocab = HashSet<String>()
        for (text in texts) {
            for (m in WORD_RE.findAll(text)) {
                val w = m.value.lowercase().replace('‐', '-')
                vocab.add(w)
                if ('-' in w) vocab.addAll(w.split('-'))
            }
        }
        return vocab
    }

    private val TAIL_WORD_RE = Regex("""([\p{L}\p{N}_]+)[-‐]$""")
    private val HEAD_WORD_RE = Regex("""^([\p{L}\p{N}_]+)""")

    /**
     * How to join two wrapped lines of one paragraph: drop this many characters
     * from the end of the right-trimmed first line, then insert the separator.
     */
    fun joiner(prev0: String, nxt0: String, vocab: Set<String>?): Pair<Int, String> {
        val prev = prev0.trimEnd()
        val nxt = nxt0.trimStart()
        if (prev.isEmpty() || nxt.isEmpty()) return 0 to ""
        if (prev.endsWith('\u00ad')) return 1 to ""
        if (prev.last() in "-‐" && prev.length >= 2 && prev[prev.length - 2].isLetter()) {
            val tail = TAIL_WORD_RE.find(prev)
            val head = HEAD_WORD_RE.find(nxt)
            if (tail != null && head != null && nxt[0].isLowerCase()) {
                val a = tail.groupValues[1]
                val b = head.groupValues[1]
                if (vocab != null) {
                    if ((a + b).lowercase() in vocab) return 1 to ""
                    if ("$a-$b".lowercase() in vocab) return 0 to ""
                }
                return 1 to ""
            }
            return 0 to ""
        }
        if (prev.endsWith('—') || nxt.startsWith('—')) return 0 to ""
        if (prev.endsWith('/') && !prev.endsWith(" /") && !nxt[0].isUpperCase()) return 0 to ""
        return 0 to " "
    }

    /** Join two wrapped lines of the same paragraph, undoing hyphenation. */
    fun joinTwo(prev0: String, nxt0: String, vocab: Set<String>? = null): String {
        val prev = prev0.trimEnd()
        val nxt = nxt0.trimStart()
        if (prev.isEmpty()) return nxt
        if (nxt.isEmpty()) return prev
        val (drop, sep) = joiner(prev, nxt, vocab)
        return prev.substring(0, prev.length - drop) + sep + nxt
    }

    fun joinLines(lines: Iterable<String>, vocab: Set<String>? = null): String {
        var out = ""
        for (line in lines) out = if (out.isNotEmpty()) joinTwo(out, line, vocab) else line.trim()
        return out
    }

    // ----------------------------------------------------- markdown blocks

    private val FENCE_RE = Regex("""^\s{0,3}(`{3,}|~{3,})""")

    /** Split Markdown into blank-line separated blocks, keeping fences whole. */
    fun splitBlocks(md: String): List<String> {
        val blocks = mutableListOf<String>()
        val cur = mutableListOf<String>()
        var fence: String? = null
        for (line in md.lines()) {
            val m = FENCE_RE.find(line)
            if (fence != null) {
                cur.add(line)
                if (m != null) {
                    val g = m.groupValues[1]
                    val stripped = line.trim()
                    if (g[0] == fence[0] && g.length >= fence.length &&
                        stripped.substring(minOf(g.length, stripped.length)).isBlank()
                    ) fence = null
                }
                continue
            }
            if (m != null) {
                fence = m.groupValues[1]
                cur.add(line)
                continue
            }
            if (line.isNotBlank()) {
                cur.add(line.trimEnd())
            } else if (cur.isNotEmpty()) {
                blocks.add(cur.joinToString("\n"))
                cur.clear()
            }
        }
        if (cur.isNotEmpty()) blocks.add(cur.joinToString("\n"))
        return blocks
    }

    private val NON_PARAGRAPH_RE = Regex(
        """^[\s\p{Z}]*(#{1,6}[\s\p{Z}]|[-+*][\s\p{Z}]|\p{Nd}{1,9}[.)][\s\p{Z}]|>|\||```|~~~|\$\$|!\[|<|\[(Figure|Diagram|Image)\b|\\\[)""",
    )

    fun isParagraph(block: String): Boolean = block.isNotBlank() && NON_PARAGRAPH_RE.find(block) == null

    private val TABLE_RULE_RE = Regex("""^\s*\|?\s*:?-{3,}""")

    fun isTable(block: String): Boolean {
        val lines = block.lines()
        return lines.size >= 2 && lines.all { it.trimStart().startsWith("|") } && TABLE_RULE_RE.find(lines[1]) != null
    }

    private val CELL_SPLIT_RE = Regex("""(?<!\\)\|""")

    private fun rowCells(row0: String): List<String> {
        var row = row0.trim()
        if (row.startsWith("|")) row = row.substring(1)
        if (row.endsWith("|") && !row.endsWith("\\|")) row = row.dropLast(1)
        return row.split(CELL_SPLIT_RE).map { it.trim() }
    }

    fun continues(prev: String, nxt: String): Boolean {
        val last = prev.trimEnd()
        val first = nxt.trimStart()
        if (last.isEmpty() || first.isEmpty()) return false
        if (last.last() in "-‐\u00ad" && first[0].isLowerCase()) return true
        return !endsSentence(last) && first[0].isLowerCase()
    }

    private val CAPTION_RE = Regex(
        """^[*_]*(fig(ure)?|table|tab|listing|algorithm|chart|plate|exhibit|scheme)\.?[\s\p{Z}]*[\p{Nd}IVX]""",
        RegexOption.IGNORE_CASE,
    )

    fun isCaption(block: String): Boolean = CAPTION_RE.find(block.trimStart()) != null

    /** Figures, code listings, tables and their captions: typesetting moves these around. */
    fun isFloat(block: String): Boolean {
        val b = block.trimStart()
        return b.startsWith("![") || b.startsWith("```") || b.startsWith("~~~") || isTable(block) || isCaption(block)
    }

    private fun mergeParagraphs(prev: String, nxt: String, vocab: Set<String>?): String {
        val cut = prev.lastIndexOf('\n')
        val head = if (cut >= 0) prev.substring(0, cut) else ""
        val lastLine = if (cut >= 0) prev.substring(cut + 1) else prev
        val nl = nxt.indexOf('\n')
        val firstLine = if (nl >= 0) nxt.substring(0, nl) else nxt
        val tail = if (nl >= 0) nxt.substring(nl + 1) else ""
        val merged = joinTwo(lastLine, firstLine, vocab)
        return (if (head.isNotEmpty()) head + "\n" else "") + merged + (if (tail.isNotEmpty()) "\n" + tail else "")
    }

    /** Re-join paragraphs that a column or page break cut in two (see mdtext.merge_continuations). */
    fun mergeContinuations(blocks: List<String>, vocab: Set<String>? = null): List<String> {
        val out = mutableListOf<String>()
        for (b in blocks) {
            if (isParagraph(b) && !isCaption(b) && b.trimStart().firstOrNull()?.isLowerCase() == true) {
                var k = out.size - 1
                while (k >= 0 && out.size - 1 - k < 4 && isFloat(out[k])) k -= 1
                if (k >= 0 && isParagraph(out[k]) && !isCaption(out[k]) && continues(out[k], b)) {
                    out[k] = mergeParagraphs(out[k], b, vocab)
                    continue
                }
            }
            out.add(b)
        }
        return out
    }

    /** Concatenate Markdown chunks (pages or page strips) into one document. */
    fun joinChunks(chunks: Iterable<String?>, vocab: Set<String>? = null, mergeTables: String = "same-header"): String {
        val out = mutableListOf<String>()
        for (chunk in chunks) {
            var blocks = splitBlocks(chunk ?: "")
            if (blocks.isEmpty()) continue
            if (out.isNotEmpty() && isTable(out.last()) && isTable(blocks[0])) {
                val prevRows = out.last().lines()
                val firstRows = blocks[0].lines()
                val prevHead = rowCells(prevRows[0])
                val firstHead = rowCells(firstRows[0])
                if (prevHead.size == firstHead.size) {
                    if (prevHead == firstHead) {
                        out[out.size - 1] = (prevRows + firstRows.drop(2)).joinToString("\n")
                        blocks = blocks.drop(1)
                    } else if (mergeTables == "any") {
                        out[out.size - 1] = (prevRows + firstRows.take(1) + firstRows.drop(2)).joinToString("\n")
                        blocks = blocks.drop(1)
                    }
                }
            }
            out.addAll(blocks)
        }
        val merged = mergeContinuations(out, vocab)
        return if (merged.isNotEmpty()) merged.joinToString("\n\n").trim() + "\n" else ""
    }
}
