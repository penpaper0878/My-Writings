package io.github.penpaper0878.pdf2md.core

/** An axis-aligned box in PDF points: x0, y0 (top), x1, y1 (bottom). */
data class Box(val x0: Double, val y0: Double, val x1: Double, val y1: Double) {
    val width get() = x1 - x0
    val height get() = y1 - y0

    companion object {
        fun union(boxes: Iterable<Box>): Box {
            var x0 = Double.POSITIVE_INFINITY
            var y0 = Double.POSITIVE_INFINITY
            var x1 = Double.NEGATIVE_INFINITY
            var y1 = Double.NEGATIVE_INFINITY
            for (b in boxes) {
                x0 = minOf(x0, b.x0); y0 = minOf(y0, b.y0)
                x1 = maxOf(x1, b.x1); y1 = maxOf(y1, b.y1)
            }
            return Box(x0, y0, x1, y1)
        }
    }
}

/** A run of text in one style. Mutable like the Python original, which edits text in place. */
class Span(
    var text: String,
    val bbox: Box,
    val size: Double,
    val bold: Boolean = false,
    val italic: Boolean = false,
    val mono: Boolean = false,
    val sup: Boolean = false,
    val strike: Boolean = false,
    val link: String? = null,
) {
    fun copy(): Span = Span(text, bbox, size, bold, italic, mono, sup, strike, link)

    fun style(plain: Boolean = false): Style =
        if (plain) Style(false, false, mono, false, false, link)
        else Style(bold, italic, mono, sup, strike, link)
}

data class Style(
    val bold: Boolean,
    val italic: Boolean,
    val mono: Boolean,
    val sup: Boolean,
    val strike: Boolean,
    val link: String?,
)

class Line(val spans: MutableList<Span>, var bbox: Box) {
    val text: String get() = spans.joinToString("") { it.text }

    /** Dominant font size, weighted by characters (Python: Counter.most_common, ties to the first seen). */
    val size: Double
        get() {
            val weights = LinkedHashMap<Double, Double>()
            for (s in spans) {
                val key = PyMath.roundHalf(s.size)
                val n = s.text.trim().length
                weights[key] = (weights[key] ?: 0.0) + (if (n > 0) n.toDouble() else 0.1)
            }
            return PyMath.mostCommon(weights) ?: 0.0
        }

    val mono: Boolean
        get() {
            val visible = spans.filter { it.text.isNotBlank() }
            return visible.isNotEmpty() && visible.all { it.mono }
        }

    val x0: Double get() = bbox.x0
}

/** The styled lines of one page, grouped in the PDF's own text blocks. */
class PageData(val number: Int, val width: Double, val height: Double, var blocks: List<List<Line>>)

/** Small helpers that reproduce Python's arithmetic exactly. */
object PyMath {
    /** Python's round(x * 2) / 2 with banker's rounding. */
    fun roundHalf(x: Double): Double = Math.rint(x * 2) / 2

    /** Key with the largest count; ties go to the earliest inserted (Counter.most_common). */
    fun <K> mostCommon(counts: Map<K, Double>): K? {
        var best: K? = null
        var bestN = Double.NEGATIVE_INFINITY
        for ((k, n) in counts) if (n > bestN) { best = k; bestN = n }
        return best
    }

    fun median(values: List<Double>): Double {
        val s = values.sorted()
        val n = s.size
        return if (n % 2 == 1) s[n / 2] else (s[n / 2 - 1] + s[n / 2]) / 2
    }
}
