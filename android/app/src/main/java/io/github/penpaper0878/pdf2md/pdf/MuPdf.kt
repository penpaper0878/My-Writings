package io.github.penpaper0878.pdf2md.pdf

import com.artifex.mupdf.fitz.ColorSpace
import com.artifex.mupdf.fitz.DefaultColorSpaces
import com.artifex.mupdf.fitz.Device
import com.artifex.mupdf.fitz.Document
import com.artifex.mupdf.fitz.Font
import com.artifex.mupdf.fitz.Image
import com.artifex.mupdf.fitz.Link
import com.artifex.mupdf.fitz.Matrix
import com.artifex.mupdf.fitz.PDFAnnotation
import com.artifex.mupdf.fitz.PDFPage
import com.artifex.mupdf.fitz.Page
import com.artifex.mupdf.fitz.Path
import com.artifex.mupdf.fitz.PathWalker
import com.artifex.mupdf.fitz.Point
import com.artifex.mupdf.fitz.Quad
import com.artifex.mupdf.fitz.Rect
import com.artifex.mupdf.fitz.Shade
import com.artifex.mupdf.fitz.StrokeState
import com.artifex.mupdf.fitz.StructuredText
import com.artifex.mupdf.fitz.StructuredTextWalker
import com.artifex.mupdf.fitz.Text
import com.artifex.mupdf.fitz.TextWalker
import io.github.penpaper0878.pdf2md.core.Box
import io.github.penpaper0878.pdf2md.core.Classify
import io.github.penpaper0878.pdf2md.core.Digital
import io.github.penpaper0878.pdf2md.core.Line
import io.github.penpaper0878.pdf2md.core.MdText
import io.github.penpaper0878.pdf2md.core.PageData
import io.github.penpaper0878.pdf2md.core.PageStats
import io.github.penpaper0878.pdf2md.core.Span

/**
 * Reading PDFs with MuPDF, the engine PyMuPDF wraps on the desktop, so a
 * typed page yields the same text, spans and boxes there and here.
 * Not thread-safe: use one document from one thread.
 */
class MuPdfDocument(path: String) : AutoCloseable {
    private val doc: Document = Document.openDocument(path)

    val needsPassword: Boolean get() = doc.needsPassword()
    fun authenticate(password: String): Boolean = doc.authenticatePassword(password)
    val pageCount: Int get() = doc.countPages()

    fun <T> withPage(number: Int, block: (Page) -> T): T {
        val page = doc.loadPage(number - 1)
        try {
            return block(page)
        } finally {
            page.destroy()
        }
    }

    override fun close() = doc.destroy()
}

/** Styled text of a page, built exactly like PyMuPDF's get_text("dict"/"rawdict"). */
object MuPdfText {
    // PyMuPDF flags used by the desktop tool: whitespace kept, clipped to the page, styles collected.
    private const val OPTIONS = "preserve-whitespace,clip,collect-styles"

    private val MONO_FONT_RE = Regex("""mono|courier|consol|menlo|code|inconsolata|fixed|typewriter|\bcmtt|sfmono""", RegexOption.IGNORE_CASE)
    private val BOLD_FONT_RE = Regex("""bold|black|heavy|semibold|demi""", RegexOption.IGNORE_CASE)
    private val ITALIC_FONT_RE = Regex("""italic|oblique|slanted""", RegexOption.IGNORE_CASE)

    private const val CF_STRIKEOUT = StructuredText.CHAR_FLAGS_STRIKEOUT
    private const val CF_SYNTHETIC = StructuredText.CHAR_FLAGS_SYNTHETIC
    private const val CF_BOLD = StructuredText.CHAR_FLAGS_BOLD
    private const val CF_FILLED = StructuredText.CHAR_FLAGS_FILLED
    private const val CF_STROKED = StructuredText.CHAR_FLAGS_STROKED

    private class Ch(
        val c: Int, val originY: Float, val font: Font, val size: Float, val box: Box,
        val argb: Int, val flags: Int, val bidi: Int,
    )

    /** PyMuPDF's JM_font_name: a "ABCDEF+" subset prefix is dropped. */
    private fun fontName(font: Font): String {
        val name = font.name ?: ""
        val plus = name.indexOf('+')
        return if (plus == 6) name.substring(7) else name
    }

    // Not Rect(Quad): in MuPDF 1.28 that constructor turns every finite quad
    // into an infinite rectangle.
    private fun rectOf(q: Quad): Box = Box(
        minOf(minOf(q.ul_x, q.ur_x), minOf(q.ll_x, q.lr_x)).toDouble(),
        minOf(minOf(q.ul_y, q.ur_y), minOf(q.ll_y, q.lr_y)).toDouble(),
        maxOf(maxOf(q.ul_x, q.ur_x), maxOf(q.ll_x, q.lr_x)).toDouble(),
        maxOf(maxOf(q.ul_y, q.ur_y), maxOf(q.ll_y, q.lr_y)).toDouble(),
    )

    private class Links(val areas: List<Pair<Box, String>>) {
        fun at(b: Box): String? {
            val cx = (b.x0 + b.x1) / 2
            val cy = (b.y0 + b.y1) / 2
            return areas.firstOrNull { (r, _) -> r.x0 - 1 <= cx && cx <= r.x1 + 1 && r.y0 - 1 <= cy && cy <= r.y1 + 1 }?.second
        }
    }

    fun extract(page: Page, number: Int): PageData {
        val bounds = page.bounds
        val links = Links(
            (page.links ?: emptyArray<Link>()).mapNotNull { link ->
                val uri = link.uri
                if (uri.isNullOrEmpty() || !Link.isExternal(uri)) null
                else link.bounds.let { Box(it.x0.toDouble(), it.y0.toDouble(), it.x1.toDouble(), it.y1.toDouble()) } to uri
            },
        )
        val stext = page.toStructuredText(OPTIONS)
        val walker = Walker(links)
        try {
            stext.walk(walker)
        } finally {
            stext.destroy()
        }
        return PageData(number, (bounds.x1 - bounds.x0).toDouble(), (bounds.y1 - bounds.y0).toDouble(), walker.blocks)
    }

    private class Walker(private val links: Links) : StructuredTextWalker {
        val blocks = mutableListOf<List<Line>>()
        private var lines = mutableListOf<Line>()
        private var chars = mutableListOf<Ch>()
        private var dir = Point(1f, 0f)
        private var wmode = 0

        override fun onImageBlock(bbox: Rect?, transform: Matrix?, image: Image?) {}
        override fun beginTextBlock(bbox: Rect?, flags: Int) { lines = mutableListOf() }
        override fun endTextBlock() {
            val merged = Digital.mergeSameBaseline(lines)
            if (merged.isNotEmpty()) blocks.add(merged)
        }
        override fun beginLine(bbox: Rect?, wmode: Int, dir: Point?) {
            chars = mutableListOf()
            this.dir = dir ?: Point(1f, 0f)
            this.wmode = wmode
        }
        override fun endLine() {
            // Rotated margin text (arXiv stamps, watermarks) is skipped.
            if (Math.abs(dir.y) > 0.1 || dir.x <= 0) return
            val spans = Digital.spaceSpans(buildSpans())
            if (spans.isNotEmpty() && spans.joinToString("") { it.text }.isNotBlank()) {
                lines.add(Line(spans, Box.union(spans.map { it.bbox })))
            }
        }
        override fun onChar(c: Int, origin: Point, font: Font, size: Float, q: Quad, argb: Int, flags: Int, bidi: Int) {
            chars.add(Ch(c, origin.y, font, size, rectOf(q), argb, flags, bidi))
        }
        override fun beginStruct(standard: String?, raw: String?, index: Int) {}
        override fun endStruct() {}
        override fun onVector(bbox: Rect?, info: StructuredTextWalker.VectorInfo?, argb: Int) {}

        /** Group characters into spans where PyMuPDF would (size, font flags, char flags, colour, font, bidi). */
        private fun buildSpans(): MutableList<Span> {
            val out = mutableListOf<Span>()
            if (chars.isEmpty()) return out
            val horizontal = wmode == 0 && dir.x == 1f && dir.y == 0f
            val firstY = chars[0].originY
            data class Key(val size: Float, val flags: Int, val charFlags: Int, val argb: Int, val font: String, val bidi: Int)
            var run = mutableListOf<Ch>()
            var key: Key? = null
            var fontFlags = 0
            fun flush() {
                if (run.isNotEmpty()) out.addAll(spansOf(run, key!!.font, fontFlags, key!!.charFlags, key!!.argb, key!!.size))
                run = mutableListOf()
            }
            for (ch in chars) {
                val sup = horizontal && ch.originY < firstY - ch.size * 0.1
                val f = (if (sup) 1 else 0) + (if (ch.font.isItalic) 2 else 0) + (if (ch.font.isSerif) 4 else 0) +
                    (if (ch.font.isMono) 8 else 0) + (if (ch.font.isBold) 16 else 0)
                val k = Key(ch.size, f, ch.flags and CF_SYNTHETIC.inv(), ch.argb, fontName(ch.font), ch.bidi)
                if (k != key) {
                    flush()
                    key = k
                    fontFlags = f
                }
                run.add(ch)
            }
            flush()
            return out
        }

        /** One PyMuPDF span, split by link areas, as the desktop tool's Span objects. */
        private fun spansOf(run: List<Ch>, font: String, flags: Int, cf: Int, argb: Int, size: Float): List<Span> {
            // Invisible text: a scanner's OCR layer, or text rendered in mode 3.
            if (font == "GlyphLessFont" || (argb ushr 24) == 0 || (cf and (CF_FILLED or CF_STROKED)) == 0) return emptyList()
            val pieces = mutableListOf<Triple<StringBuilder, MutableList<Box>, String?>>()
            if (links.areas.isEmpty()) {
                val sb = StringBuilder()
                for (ch in run) sb.appendCodePoint(ch.c)
                pieces.add(Triple(sb, run.map { it.box }.toMutableList(), null))
            } else {
                for (ch in run) {
                    val box = ch.box
                    val isSpace = Character.isWhitespace(ch.c) || Character.isSpaceChar(ch.c)
                    val uri = if (!isSpace) links.at(box) else pieces.lastOrNull()?.third
                    if (pieces.isNotEmpty() && pieces.last().third == uri) {
                        pieces.last().first.appendCodePoint(ch.c)
                        pieces.last().second.add(box)
                    } else {
                        pieces.add(Triple(StringBuilder().appendCodePoint(ch.c), mutableListOf(box), uri))
                    }
                }
            }
            val out = mutableListOf<Span>()
            fun add(text0: String, bbox: Box, link: String?) {
                val text = MdText.normalizeText(text0)
                if (text.isEmpty()) return
                out.add(
                    Span(
                        text = text, bbox = bbox, size = size.toDouble(),
                        bold = (flags and 16) != 0 || BOLD_FONT_RE.containsMatchIn(font) || (cf and CF_BOLD) != 0,
                        italic = (flags and 2) != 0 || ITALIC_FONT_RE.containsMatchIn(font),
                        mono = (flags and 8) != 0 || MONO_FONT_RE.containsMatchIn(font),
                        sup = (flags and 1) != 0,
                        strike = (cf and CF_STRIKEOUT) != 0,
                        link = link,
                    ),
                )
            }
            for ((sb, boxes, uri) in pieces) {
                val text = sb.toString()
                // A trailing space inside the link belongs outside it.
                if (uri != null && text.endsWith(" ") && text.length > 1) {
                    val core = text.trimEnd()
                    add(core, Box.union(boxes), uri)
                    add(" ".repeat(text.length - core.length), boxes.last(), null)
                } else {
                    add(text, Box.union(boxes), uri)
                }
            }
            return out
        }
    }
}

/**
 * Measurements for deciding how a page is read (port of classify.py's
 * profile_page), gathered by running the page contents through a device.
 */
object MuPdfStats {
    private const val GRID = 64

    fun measure(page: Page, number: Int): PageStats {
        val bounds = page.bounds
        val dev = StatsDevice(bounds)
        try {
            page.runPageContents(dev, Matrix.Identity(), null)
        } finally {
            dev.close()
            dev.destroy()
        }
        var inkAnnots = 0
        if (page is PDFPage) {
            for (a in page.annotations ?: emptyArray<PDFAnnotation>()) {
                if (a.type == PDFAnnotation.TYPE_INK) {
                    inkAnnots += 1
                    dev.strokes += 1
                    dev.mark(dev.inkMask, a.bounds)
                }
            }
        }
        return PageStats(
            number = number,
            visibleChars = dev.visible,
            invisibleChars = dev.invisible,
            garbageChars = dev.garbage,
            imageCoverage = dev.imageMask.count { it }.toDouble() / (GRID * GRID),
            largestImage = dev.largestImage,
            inkStrokes = dev.strokes,
            inkCoverage = dev.inkMask.count { it }.toDouble() / (GRID * GRID),
            inkAnnotations = inkAnnots,
            drawings = dev.drawings,
        )
    }

    private class StatsDevice(private val page: Rect) : Device() {
        var visible = 0
        var invisible = 0
        var garbage = 0
        var strokes = 0
        var drawings = 0
        var largestImage = 0.0
        val imageMask = BooleanArray(GRID * GRID)
        val inkMask = BooleanArray(GRID * GRID)
        private var lastFill: Rect? = null
        private val pageArea = maxOf((page.x1 - page.x0).toDouble() * (page.y1 - page.y0), 1.0)

        fun mark(mask: BooleanArray, r: Rect?) {
            if (r == null || r.isInfinite || r.isEmpty) return
            val w = maxOf(page.x1 - page.x0, 1f)
            val h = maxOf(page.y1 - page.y0, 1f)
            val x0 = maxOf(0, ((r.x0 - page.x0) / w * GRID).toInt())
            val y0 = maxOf(0, ((r.y0 - page.y0) / h * GRID).toInt())
            val x1 = minOf(GRID, Math.ceil(((r.x1 - page.x0) / w * GRID).toDouble()).toInt())
            val y1 = minOf(GRID, Math.ceil(((r.y1 - page.y0) / h * GRID).toDouble()).toInt())
            for (y in y0 until y1) for (x in x0 until x1) mask[y * GRID + x] = true
        }

        private fun countText(text: Text?, ctm: Matrix?, hidden: Boolean) {
            if (text == null) return
            text.walk(object : TextWalker {
                override fun showGlyph(font: Font?, trm: Matrix?, glyph: Int, unicode: Int, wmode: Boolean) {
                    if (unicode < 0 || Character.isWhitespace(unicode) || Character.isSpaceChar(unicode)) return
                    if (trm != null && ctm != null) {
                        val x = trm.e * ctm.a + trm.f * ctm.c + ctm.e
                        val y = trm.e * ctm.b + trm.f * ctm.d + ctm.f
                        if (x < page.x0 - 1 || x > page.x1 + 1 || y < page.y0 - 1 || y > page.y1 + 1) return
                    }
                    if (hidden) {
                        invisible += 1
                    } else {
                        visible += 1
                        if (Classify.isGarbage(unicode)) garbage += 1
                    }
                }
            })
        }

        /**
         * A path's segments and its box: every point, control points included,
         * through the matrix (as PyMuPDF measures drawings). Path.getBounds
         * cannot be used for fills: the Java binding refuses a null stroke.
         */
        private class Shape(private val ctm: Matrix?) : PathWalker {
            var items = 0
            var curves = 0
            var closed = false
            private var x0 = Float.POSITIVE_INFINITY
            private var y0 = Float.POSITIVE_INFINITY
            private var x1 = Float.NEGATIVE_INFINITY
            private var y1 = Float.NEGATIVE_INFINITY

            private fun point(x: Float, y: Float) {
                val m = ctm
                val tx = if (m == null) x else x * m.a + y * m.c + m.e
                val ty = if (m == null) y else x * m.b + y * m.d + m.f
                if (tx < x0) x0 = tx
                if (ty < y0) y0 = ty
                if (tx > x1) x1 = tx
                if (ty > y1) y1 = ty
            }

            override fun moveTo(x: Float, y: Float) = point(x, y)
            override fun lineTo(x: Float, y: Float) { items += 1; point(x, y) }
            override fun curveTo(cx1: Float, cy1: Float, cx2: Float, cy2: Float, ex: Float, ey: Float) {
                items += 1
                curves += 1
                point(cx1, cy1)
                point(cx2, cy2)
                point(ex, ey)
            }
            override fun closePath() { closed = true }

            val bounds: Rect? get() = if (x0 > x1 || y0 > y1) null else Rect(x0, y0, x1, y1)
        }

        private fun shape(path: Path, ctm: Matrix?) = Shape(ctm).also { path.walk(it) }

        private fun same(a: Rect?, b: Rect?) =
            a != null && b != null && a.x0 == b.x0 && a.y0 == b.y0 && a.x1 == b.x1 && a.y1 == b.y1

        override fun fillPath(path: Path?, evenOdd: Boolean, ctm: Matrix?, cs: ColorSpace?, color: FloatArray?, alpha: Float, cp: Int) {
            if (path == null) return
            drawings += 1
            lastFill = shape(path, ctm).bounds
        }

        override fun strokePath(path: Path?, stroke: StrokeState?, ctm: Matrix?, cs: ColorSpace?, color: FloatArray?, alpha: Float, cp: Int) {
            if (path == null) return
            val shape = shape(path, ctm)
            val bounds = shape.bounds ?: return
            // A filled and stroked shape is one drawing, and filled shapes are not pen strokes.
            if (same(bounds, lastFill)) {
                lastFill = null
                return
            }
            drawings += 1
            val w = bounds.x1 - bounds.x0
            val h = bounds.y1 - bounds.y0
            val pageSized = w > 0 && h > 0 && minOf(w, h) > 300
            if (shape.items >= 5 && !shape.closed && !pageSized && shape.curves >= 0.6 * shape.items) {
                strokes += 1
                mark(inkMask, bounds)
            }
        }

        override fun fillText(text: Text?, ctm: Matrix?, cs: ColorSpace?, color: FloatArray?, alpha: Float, cp: Int) =
            countText(text, ctm, hidden = alpha == 0f)
        override fun strokeText(text: Text?, stroke: StrokeState?, ctm: Matrix?, cs: ColorSpace?, color: FloatArray?, alpha: Float, cp: Int) =
            countText(text, ctm, hidden = alpha == 0f)
        override fun clipText(text: Text?, ctm: Matrix?) = countText(text, ctm, hidden = false)
        override fun clipStrokeText(text: Text?, stroke: StrokeState?, ctm: Matrix?) = countText(text, ctm, hidden = false)
        override fun ignoreText(text: Text?, ctm: Matrix?) = countText(text, ctm, hidden = true)

        private fun image(ctm: Matrix?) {
            if (ctm == null) return
            // An image fills the unit square transformed by its matrix.
            val xs = floatArrayOf(ctm.e, ctm.a + ctm.e, ctm.c + ctm.e, ctm.a + ctm.c + ctm.e)
            val ys = floatArrayOf(ctm.f, ctm.b + ctm.f, ctm.d + ctm.f, ctm.b + ctm.d + ctm.f)
            val r = Rect(
                maxOf(xs.min(), page.x0), maxOf(ys.min(), page.y0),
                minOf(xs.max(), page.x1), minOf(ys.max(), page.y1),
            )
            if (r.x1 <= r.x0 || r.y1 <= r.y0) return
            mark(imageMask, r)
            largestImage = maxOf(largestImage, (r.x1 - r.x0).toDouble() * (r.y1 - r.y0) / pageArea)
        }

        override fun fillImage(img: Image?, ctm: Matrix?, alpha: Float, cp: Int) = image(ctm)
        override fun fillImageMask(img: Image?, ctm: Matrix?, cs: ColorSpace?, color: FloatArray?, alpha: Float, cp: Int) = image(ctm)

        override fun close() {}
        override fun clipPath(path: Path?, evenOdd: Boolean, ctm: Matrix?) {}
        override fun clipStrokePath(path: Path?, stroke: StrokeState?, ctm: Matrix?) {}
        override fun fillShade(shd: Shade?, ctm: Matrix?, alpha: Float, cp: Int) {}
        override fun clipImageMask(img: Image?, ctm: Matrix?) {}
        override fun popClip() {}
        override fun beginMask(area: Rect?, luminosity: Boolean, cs: ColorSpace?, bc: FloatArray?, cp: Int) {}
        override fun endMask() {}
        override fun beginGroup(area: Rect?, cs: ColorSpace?, isolated: Boolean, knockout: Boolean, blendmode: Int, alpha: Float) {}
        override fun endGroup() {}
        override fun beginTile(area: Rect?, view: Rect?, xstep: Float, ystep: Float, ctm: Matrix?, id: Int, docId: Int): Int = 0
        override fun endTile() {}
        override fun renderFlags(set: Int, clear: Int) {}
        override fun setDefaultColorSpaces(dcs: DefaultColorSpaces?) {}
        override fun beginLayer(name: String?) {}
        override fun endLayer() {}
        override fun beginStructure(standard: Int, raw: String?, idx: Int) {}
        override fun endStructure() {}
        override fun beginMetatext(meta: Int, text: String?) {}
        override fun endMetatext() {}
    }
}
