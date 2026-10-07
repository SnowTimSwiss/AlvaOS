package org.alvaos.photos

import org.junit.Assert.assertEquals
import org.junit.Test

class NamesTest {
    @Test
    fun aTakenNameGetsANumberBeforeItsEnding() {
        assertEquals("a.jpg", freeName(emptySet(), "a.jpg"))
        assertEquals("a (2).jpg", freeName(setOf("a.jpg"), "a.jpg"))
        assertEquals("a (3).jpg", freeName(setOf("a.jpg", "a (2).jpg"), "a.jpg"))
        assertEquals("notes (2)", freeName(setOf("notes"), "notes"))
        assertEquals(".bashrc (2)", freeName(setOf(".bashrc"), ".bashrc"))
        assertEquals("a.tar (2).gz", freeName(setOf("a.tar.gz"), "a.tar.gz"))
    }

    @Test
    fun aNameFromAnotherAppIsMadeSafe() {
        assertEquals("a_b.pdf", fileNameOf("a/b.pdf", "x"))
        assertEquals("evil.txt", fileNameOf("..evil.txt", "x"))
        assertEquals("shared", fileNameOf(null, "shared"))
        assertEquals("shared", fileNameOf("   ", "shared"))
        assertEquals(200, fileNameOf("a".repeat(500), "x").length)
    }
}
