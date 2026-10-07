package org.alvaos.photos

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

class StaleTest {
    private val day = 24 * 60 * 60 * 1000L

    @Test
    fun aBackupThatWorkedRecentlyOrNeverRanIsNotWarnedAbout() {
        assertNull(staleDays(10 * day, 9 * day, 0))
        assertNull(staleDays(10 * day, 8 * day, 0))          // two days
        assertNull(staleDays(10 * day, 0, 0))                // never ran
    }

    @Test
    fun threeDaysWithoutABackupIsToldOncePerDay() {
        assertEquals(3, staleDays(10 * day, 7 * day, 0))
        assertEquals(5, staleDays(10 * day, 5 * day, 0))
        assertNull(staleDays(10 * day, 5 * day, 10 * day - day / 2))   // told twelve hours ago
        assertEquals(5, staleDays(10 * day, 5 * day, 9 * day - 1))     // told over a day ago
    }
}
