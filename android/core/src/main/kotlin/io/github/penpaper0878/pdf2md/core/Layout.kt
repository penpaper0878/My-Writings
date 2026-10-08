package io.github.penpaper0878.pdf2md.core

/**
 * Reading order for page elements (text blocks, tables, images): a recursive
 * XY-cut that keeps multi-column pages column by column. Port of layout.py.
 */
object Layout {
    private const val MIN_GUTTER = 6.0
    private const val MIN_COLUMN_SHARE = 0.12

    private fun <T> bbox(items: List<T>, key: (T) -> Box) = Box.union(items.map(key))

    private fun <T> split(items: List<T>, key: (T) -> Box, horizontal: Boolean, minGap: Double): List<MutableList<T>> {
        val lo: (Box) -> Double = if (horizontal) { b -> b.x0 } else { b -> b.y0 }
        val hi: (Box) -> Double = if (horizontal) { b -> b.x1 } else { b -> b.y1 }
        val groups = mutableListOf<MutableList<T>>()
        var end: Double? = null
        for (it in items.sortedBy { lo(key(it)) }) {
            val b = key(it)
            if (end == null || lo(b) > end + minGap) {
                groups.add(mutableListOf(it))
                end = hi(b)
            } else {
                groups.last().add(it)
                end = maxOf(end, hi(b))
            }
        }
        return groups
    }

    private fun <T> columns(items: List<T>, key: (T) -> Box, nlines: (T) -> Int): List<List<T>>? {
        if (items.size < 2) return null
        val groups = split(items, key, horizontal = true, minGap = MIN_GUTTER)
        if (groups.size < 2) return null
        val all = bbox(items, key)
        val width = maxOf(all.x1 - all.x0, 1.0)
        for (g in groups) {
            val gb = bbox(g, key)
            if ((gb.x1 - gb.x0) / width < MIN_COLUMN_SHARE) return null
        }
        for (i in 0 until groups.size - 1) if (rowAligned(groups[i], groups[i + 1], key, nlines)) return null
        return groups
    }

    /** Side-by-side groups that line up item for item are a form or grid, read across. */
    private fun <T> rowAligned(a: List<T>, b: List<T>, key: (T) -> Box, nlines: (T) -> Int): Boolean {
        val (small, big) = if (a.size <= b.size) a to b else b to a
        if (small.size < 2) return false
        var hits = 0
        for (it in small) {
            if (nlines(it) > 1) continue
            val bi = key(it)
            val hi = bi.y1 - bi.y0
            for (other in big) {
                if (nlines(other) > 2) continue
                val bo = key(other)
                val ho = bo.y1 - bo.y0
                if (Math.abs(bi.y0 - bo.y0) <= maxOf(2.0, 0.3 * minOf(hi, ho))) {
                    hits += 1
                    break
                }
            }
        }
        return hits.toDouble() / small.size >= 0.6
    }

    /** Top-to-bottom, and left-to-right for items sitting on the same line. */
    private fun <T> rowSort(items: List<T>, key: (T) -> Box): List<T> {
        val ordered = items.sortedWith(compareBy<T>({ key(it).y0 }, { key(it).x0 }))
        val rows = mutableListOf<MutableList<T>>()
        var rowTop = 0.0
        var rowBottom: Double? = null
        for (it in ordered) {
            val b = key(it)
            val h = maxOf(b.y1 - b.y0, 1.0)
            if (rows.isNotEmpty() && rowBottom != null) {
                val overlap = minOf(rowBottom, b.y1) - maxOf(rowTop, b.y0)
                if (overlap >= 0.5 * minOf(h, maxOf(rowBottom - rowTop, 1.0))) {
                    rows.last().add(it)
                    rowBottom = maxOf(rowBottom, b.y1)
                    continue
                }
            }
            rows.add(mutableListOf(it))
            rowTop = b.y0
            rowBottom = b.y1
        }
        return rows.flatMap { row -> row.sortedBy { key(it).x0 } }
    }

    fun <T> readingOrder(items: List<T>, key: (T) -> Box, nlines: (T) -> Int = { 1 }): List<T> {
        if (items.size <= 1) return items.toList()
        columns(items, key, nlines)?.let { cols -> return cols.flatMap { readingOrder(it, key, nlines) } }
        val bands = split(items, key, horizontal = false, minGap = 0.5)
        if (bands.size > 1) {
            val merged = mutableListOf<List<T>>(bands[0])
            for (band in bands.drop(1)) {
                if (columns(merged.last() + band, key, nlines) != null) merged[merged.size - 1] = merged.last() + band
                else merged.add(band)
            }
            if (merged.size > 1) return merged.flatMap { readingOrder(it, key, nlines) }
            columns(merged[0], key, nlines)?.let { cols -> return cols.flatMap { readingOrder(it, key, nlines) } }
        }
        return rowSort(items, key)
    }
}
