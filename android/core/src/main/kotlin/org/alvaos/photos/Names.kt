package org.alvaos.photos

/**
 * The name a file is saved under without taking another's: "photo.jpg", then
 * "photo (2).jpg", "photo (3).jpg", … The NAS never saves over a file.
 */
fun freeName(taken: Set<String>, name: String): String {
    if (name !in taken) return name
    val dot = name.lastIndexOf('.')
    val (stem, ext) = if (dot > 0) name.substring(0, dot) to name.substring(dot) else name to ""
    var n = 2
    while ("$stem ($n)$ext" in taken) n += 1
    return "$stem ($n)$ext"
}

/** What another app calls a file may hold characters the NAS does not take. */
fun fileNameOf(raw: String?, fallback: String): String {
    val clean = (raw ?: "").replace(Regex("[/\\\\\u0000-\u001f]"), "_").trim().trimStart('.')
    return clean.take(200).ifEmpty { fallback }
}
