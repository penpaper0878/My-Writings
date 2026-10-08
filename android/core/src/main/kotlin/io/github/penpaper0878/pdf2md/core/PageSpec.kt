package io.github.penpaper0878.pdf2md.core

/** Page ranges like "1-3,7,10-" (1-based), as the desktop tool's --pages. */
object PageSpec {
    fun parse(spec: String, pageCount: Int): List<Int> {
        val pages = sortedSetOf<Int>()
        for (raw in spec.replace(" ", "").split(",")) {
            if (raw.isEmpty()) continue
            if ('-' in raw) {
                val (a, b) = raw.split("-", limit = 2)
                val lo = if (a.isEmpty()) 1 else a.toIntOrNull() ?: throw IllegalArgumentException("bad page range '$raw'")
                val hi = if (b.isEmpty()) pageCount else b.toIntOrNull() ?: throw IllegalArgumentException("bad page range '$raw'")
                require(lo >= 1 && hi >= lo) { "bad page range '$raw'" }
                pages.addAll(lo..hi)
            } else {
                val n = raw.toIntOrNull() ?: throw IllegalArgumentException("bad page number '$raw'")
                require(n >= 1) { "bad page number '$raw'" }
                pages.add(n)
            }
        }
        return pages.filter { it <= pageCount }
    }
}
