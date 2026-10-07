package org.alvaos.photos

private const val DAY = 24 * 60 * 60 * 1000L

/**
 * A backup that has not worked for a while is easy to miss: when did it last, and has the person been
 * told today? Returns the number of days to tell them, or null (nothing yet, or told already).
 * `lastSync` 0 means it never ran, which is not a reason to warn.
 */
fun staleDays(now: Long, lastSync: Long, warnedAt: Long, after: Int = 3): Int? {
    if (lastSync <= 0) return null
    val days = ((now - lastSync) / DAY).toInt()
    if (days < after) return null
    if (warnedAt > 0 && now - warnedAt < DAY) return null
    return days
}
