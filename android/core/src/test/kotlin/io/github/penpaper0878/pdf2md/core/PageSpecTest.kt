package io.github.penpaper0878.pdf2md.core

import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFailsWith

class PageSpecTest {
    @Test
    fun ranges() {
        assertEquals(listOf(1, 2, 3, 7, 10, 11, 12), PageSpec.parse("1-3,7,10-", 12))
        assertEquals(listOf(2, 3), PageSpec.parse(" 3, 2 ,99", 5))
        assertEquals(listOf(1, 2), PageSpec.parse("-2", 5))
    }

    @Test
    fun badSpecs() {
        assertFailsWith<IllegalArgumentException> { PageSpec.parse("x", 5) }
        assertFailsWith<IllegalArgumentException> { PageSpec.parse("3-1", 5) }
        assertFailsWith<IllegalArgumentException> { PageSpec.parse("0", 5) }
    }
}
