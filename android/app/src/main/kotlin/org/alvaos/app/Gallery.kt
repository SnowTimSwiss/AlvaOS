package org.alvaos.app

import android.Manifest
import android.content.ContentUris
import android.content.Context
import android.content.pm.PackageManager
import android.net.Uri
import android.provider.MediaStore
import org.alvaos.photos.Library
import org.alvaos.photos.LocalPhoto
import java.io.InputStream

/**
 * The phone's gallery through MediaStore: pictures and videos, by album
 * (Android's "bucket": Camera, Screenshots, WhatsApp Images, …). Ids are
 * "i<number>" for pictures and "v<number>" for videos.
 */
class Gallery(private val context: Context) : Library {

    private fun collection(kind: Char): Uri =
        if (kind == 'v') MediaStore.Video.Media.getContentUri(MediaStore.VOLUME_EXTERNAL)
        else MediaStore.Images.Media.getContentUri(MediaStore.VOLUME_EXTERNAL)

    fun uri(id: String): Uri = ContentUris.withAppendedId(collection(id[0]), id.substring(1).toLong())

    private fun query(kind: Char, albums: Set<String>?): List<LocalPhoto> {
        val out = mutableListOf<LocalPhoto>()
        val columns = arrayOf(
            MediaStore.MediaColumns._ID, MediaStore.MediaColumns.BUCKET_DISPLAY_NAME,
            MediaStore.MediaColumns.DISPLAY_NAME, MediaStore.MediaColumns.SIZE,
            MediaStore.MediaColumns.DATE_TAKEN, MediaStore.MediaColumns.DATE_MODIFIED,
        )
        context.contentResolver.query(collection(kind), columns, null, null, null)?.use { c ->
            while (c.moveToNext()) {
                val album = c.getString(1) ?: "Pictures"
                if (albums != null && album !in albums) continue
                val size = c.getLong(3)
                if (size <= 0) continue                          // still being written
                val taken = c.getLong(4)
                val modified = if (taken > 0) taken else c.getLong(5) * 1000
                out += LocalPhoto("$kind${c.getLong(0)}", album, c.getString(2) ?: "${c.getLong(0)}", size, modified)
            }
        }
        return out
    }

    /** Which of these are still on the phone. */
    fun existing(ids: Collection<String>): Set<String> = ids.filter { id ->
        context.contentResolver.query(uri(id), arrayOf(MediaStore.MediaColumns._ID), null, null, null)
            ?.use { it.moveToFirst() } ?: false
    }.toSet()

    /** Android 12+: the person allowed this app to delete pictures without asking each time. */
    fun maySilentlyDelete(): Boolean = android.os.Build.VERSION.SDK_INT >= 31 && MediaStore.canManageMedia(context)

    override fun albums(): List<String> =
        (query('i', null) + query('v', null)).groupingBy { it.album }.eachCount()
            .entries.sortedByDescending { it.value }.map { it.key }

    fun counts(): Map<String, Int> = (query('i', null) + query('v', null)).groupingBy { it.album }.eachCount()

    override fun photos(albums: Set<String>): List<LocalPhoto> =
        if (albums.isEmpty()) emptyList() else query('i', albums) + query('v', albums)

    override fun open(id: String, offset: Long): InputStream {
        var uri = uri(id)
        // The original, with where it was taken, when the person allowed that.
        if (context.checkSelfPermission(Manifest.permission.ACCESS_MEDIA_LOCATION) == PackageManager.PERMISSION_GRANTED) {
            uri = MediaStore.setRequireOriginal(uri)
        }
        val input = context.contentResolver.openInputStream(uri) ?: throw java.io.IOException("$id cannot be opened")
        var left = offset
        while (left > 0) {
            val skipped = input.skip(left)
            if (skipped <= 0) break
            left -= skipped
        }
        return input
    }
}
