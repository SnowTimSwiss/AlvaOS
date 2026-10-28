package org.alvaos.app

import android.content.Context
import android.database.Cursor
import android.database.MatrixCursor
import android.os.CancellationSignal
import android.os.Handler
import android.os.HandlerThread
import android.os.ParcelFileDescriptor
import android.provider.DocumentsContract
import android.provider.DocumentsContract.Document
import android.provider.DocumentsContract.Root
import android.provider.DocumentsProvider
import android.util.Log
import android.webkit.MimeTypeMap
import org.alvaos.photos.FileEntry
import org.alvaos.photos.HubClient
import org.alvaos.photos.HubException
import org.alvaos.photos.Upload
import java.io.ByteArrayInputStream
import java.io.File
import java.io.FileInputStream
import java.io.FileNotFoundException
import java.io.IOException
import java.security.MessageDigest
import java.util.concurrent.ConcurrentHashMap

/**
 * AlvaOS in the phone's own Files app (and in every app that opens or saves files): one entry,
 * "AlvaOS – <NAS>", with the shared folders of the person in it. Open, save, create, rename,
 * move and delete work there as in any folder of the phone; it goes through the Hub (and
 * through AlvaOS Link away from home), with the phone's own session.
 *
 * A document's id is its place: `/` is the top (the shares), `/Family` a share,
 * `/Family/Photos/a.jpg` a file in it.
 */
class AlvaDocuments : DocumentsProvider() {
    companion object {
        const val AUTHORITY = "uk.timserver.alvaos.documents"
        private const val ROOT = "alvaos"
        private const val TAG = "AlvaDocuments"
        private const val CACHE_LIMIT = 300L * 1024 * 1024
        private const val FRESH_MS = 60_000L

        /** For the common endings, when the system's own list does not know them. */
        private val COMMON_TYPES = mapOf(
            "txt" to "text/plain", "md" to "text/markdown", "csv" to "text/csv", "html" to "text/html", "json" to "application/json",
            "pdf" to "application/pdf", "zip" to "application/zip", "jpg" to "image/jpeg", "jpeg" to "image/jpeg", "png" to "image/png",
            "gif" to "image/gif", "webp" to "image/webp", "heic" to "image/heic", "mp4" to "video/mp4", "mkv" to "video/x-matroska",
            "mov" to "video/quicktime", "mp3" to "audio/mpeg", "m4a" to "audio/mp4", "flac" to "audio/flac", "ogg" to "audio/ogg",
        )

        private val ROOT_COLUMNS = arrayOf(Root.COLUMN_ROOT_ID, Root.COLUMN_FLAGS, Root.COLUMN_ICON, Root.COLUMN_TITLE,
            Root.COLUMN_SUMMARY, Root.COLUMN_DOCUMENT_ID)
        private val DOC_COLUMNS = arrayOf(Document.COLUMN_DOCUMENT_ID, Document.COLUMN_MIME_TYPE, Document.COLUMN_DISPLAY_NAME,
            Document.COLUMN_LAST_MODIFIED, Document.COLUMN_FLAGS, Document.COLUMN_SIZE)

        /** Signed in or out: the entry in the Files app comes or goes. */
        fun rootsChanged(context: Context) {
            try { context.contentResolver.notifyChange(DocumentsContract.buildRootsUri(AUTHORITY), null) } catch (e: Exception) { /* nobody listens */ }
        }

        /** A place: the share and the path in it; both "" for the top. */
        data class Place(val share: String, val path: String) {
            val top: Boolean get() = share.isEmpty()
            val name: String get() = path.substringAfterLast('/').ifEmpty { share }
            val parent: Place get() = if (path.isEmpty()) Place("", "") else Place(share, path.substringBeforeLast('/', ""))
            fun child(name: String) = Place(share, if (path.isEmpty()) name else "$path/$name")
            val id: String get() = if (top) "/" else "/" + listOf(share, path).filter { it.isNotEmpty() }.joinToString("/")
        }

        fun place(id: String): Place {
            val clean = id.trim('/')
            if (clean.isEmpty()) return Place("", "")
            return Place(clean.substringBefore('/'), clean.substringAfter('/', ""))
        }

        /** `a.txt`, then `a (1).txt`, `a (2).txt`, … the first the folder does not have. */
        fun unique(name: String, taken: Set<String>): String {
            if (name !in taken) return name
            val dot = name.lastIndexOf('.').takeIf { it > 0 } ?: name.length
            val (stem, ext) = name.substring(0, dot) to name.substring(dot)
            return generateSequence(1) { it + 1 }.map { "$stem ($it)$ext" }.first { it !in taken }
        }
    }

    private val known = ConcurrentHashMap<String, FileEntry>()       // by document id: what the last listing said
    private val access = ConcurrentHashMap<String, String>()          // share → "write" / "read"
    @Volatile private var server = ""
    @Volatile private var serverAt = 0L
    private val writer by lazy { HandlerThread("alvaos-documents").apply { start() }.let { Handler(it.looper) } }

    override fun onCreate(): Boolean = true

    private fun store() = Store(context!!)

    private fun signedIn(): Boolean = store().let { it.signedIn && !it.demo }

    /** The Hub, at the address that answers (home or through Link); the choice is kept for a minute. */
    private fun hub(): HubClient {
        val s = store()
        if (!s.signedIn || s.demo) throw FileNotFoundException("AlvaOS is not signed in.")
        if (server.isEmpty() || System.currentTimeMillis() - serverAt > FRESH_MS) {
            server = s.pickServer()
            serverAt = System.currentTimeMillis()
        }
        return HubClient(server, s.token)
    }

    /** Hub errors as what a document provider may throw. */
    private inline fun <T> hubCall(block: (HubClient) -> T): T = try {
        block(hub())
    } catch (e: HubException) {
        if (e.signedOut) throw FileNotFoundException("AlvaOS was signed out. Open the app to connect again.")
        throw FileNotFoundException(e.message ?: "The NAS did not answer.")
    } catch (e: FileNotFoundException) {
        throw e
    } catch (e: IOException) {
        serverAt = 0                                                  // try the other way next time
        throw FileNotFoundException("The NAS cannot be reached (${e.message}).")
    }

    private fun writable(share: String): Boolean {
        if (share.isEmpty()) return false
        access[share]?.let { return it == "write" }
        hubCall { hub -> hub.me().shares.forEach { access[it.name] = it.access } }
        return access[share] == "write"
    }

    // ── Roots and documents ──────────────────────────────────────────────

    override fun queryRoots(projection: Array<String>?): Cursor {
        val cursor = MatrixCursor(projection ?: ROOT_COLUMNS)
        if (!signedIn()) return cursor
        val s = store()
        cursor.newRow().apply {
            add(Root.COLUMN_ROOT_ID, ROOT)
            add(Root.COLUMN_FLAGS, Root.FLAG_SUPPORTS_IS_CHILD)
            add(Root.COLUMN_ICON, R.mipmap.ic_launcher)
            add(Root.COLUMN_TITLE, "AlvaOS – ${s.nasName.ifEmpty { "NAS" }}")
            add(Root.COLUMN_SUMMARY, s.user)
            add(Root.COLUMN_DOCUMENT_ID, "/")
        }
        return cursor
    }

    private fun mimeOf(name: String): String {
        val ext = name.substringAfterLast('.', "").lowercase()
        return MimeTypeMap.getSingleton().getMimeTypeFromExtension(ext) ?: COMMON_TYPES[ext] ?: "application/octet-stream"
    }

    private fun addRow(cursor: MatrixCursor, place: Place, entry: FileEntry?) {
        val folder = place.top || place.path.isEmpty() || entry?.isFolder == true
        val write = writable(place.share)
        var flags = 0
        if (write && !place.top) {
            flags = if (folder) Document.FLAG_DIR_SUPPORTS_CREATE else Document.FLAG_SUPPORTS_WRITE
            if (place.path.isNotEmpty()) flags = flags or Document.FLAG_SUPPORTS_DELETE or Document.FLAG_SUPPORTS_RENAME or Document.FLAG_SUPPORTS_MOVE
        }
        cursor.newRow().apply {
            add(Document.COLUMN_DOCUMENT_ID, place.id)
            add(Document.COLUMN_DISPLAY_NAME, if (place.top) store().nasName.ifEmpty { "AlvaOS" } else place.name)
            add(Document.COLUMN_MIME_TYPE, if (folder) Document.MIME_TYPE_DIR else mimeOf(place.name))
            add(Document.COLUMN_FLAGS, flags)
            add(Document.COLUMN_SIZE, if (folder) null else entry?.size_bytes)
            add(Document.COLUMN_LAST_MODIFIED, entry?.modified?.takeIf { it > 0 })
        }
    }

    override fun queryDocument(documentId: String, projection: Array<String>?): Cursor {
        val cursor = MatrixCursor(projection ?: DOC_COLUMNS)
        val place = place(documentId)
        if (place.top || place.path.isEmpty()) {
            addRow(cursor, place, null)
            return cursor
        }
        val entry = known[place.id] ?: hubCall { hub ->
            val listing = hub.list(place.share, place.parent.path)
            listing.forEach { known[place.parent.child(it.name).id] = it }
            listing.firstOrNull { it.name == place.name }
        } ?: throw FileNotFoundException("${place.id} is not there any more.")
        addRow(cursor, place, entry)
        return cursor
    }

    override fun queryChildDocuments(parentDocumentId: String, projection: Array<String>?, sortOrder: String?): Cursor {
        val cursor = MatrixCursor(projection ?: DOC_COLUMNS)
        cursor.setNotificationUri(context!!.contentResolver, DocumentsContract.buildChildDocumentsUri(AUTHORITY, parentDocumentId))
        val parent = place(parentDocumentId)
        if (parent.top) {
            hubCall { hub -> hub.me().shares }.also { shares -> shares.forEach { access[it.name] = it.access } }
                .forEach { addRow(cursor, Place(it.name, ""), null) }
            return cursor
        }
        hubCall { hub -> hub.list(parent.share, parent.path) }.forEach { entry ->
            val child = parent.child(entry.name)
            known[child.id] = entry
            addRow(cursor, child, entry)
        }
        return cursor
    }

    override fun isChildDocument(parentDocumentId: String, documentId: String): Boolean {
        val parent = parentDocumentId.trimEnd('/')
        return documentId != parentDocumentId && documentId.startsWith("$parent/")
    }

    // ── Opening and saving ───────────────────────────────────────────────

    private fun cacheDir(): File = File(context!!.cacheDir, "documents").apply { mkdirs() }

    private fun sha(text: String) = MessageDigest.getInstance("SHA-1").digest(text.toByteArray()).joinToString("") { "%02x".format(it) }

    /** The oldest files go when the cache is over its limit. */
    private fun trimCache() {
        val files = cacheDir().listFiles()?.sortedBy { it.lastModified() } ?: return
        var total = files.sumOf { it.length() }
        for (f in files) {
            if (total <= CACHE_LIMIT) break
            total -= f.length()
            f.delete()
        }
    }

    override fun openDocument(documentId: String, mode: String, signal: CancellationSignal?): ParcelFileDescriptor {
        val place = place(documentId)
        if (place.top || place.path.isEmpty()) throw FileNotFoundException("$documentId is a folder.")
        val modes = ParcelFileDescriptor.parseMode(mode)
        val writing = mode.any { it == 'w' || it == 'a' }
        if (writing) {
            if (!writable(place.share)) throw SecurityException("This shared folder is read only.")
            return openForWriting(place, mode, modes)
        }
        val entry = known[place.id] ?: queryDocument(documentId, null).let { known[place.id] }
        val stamp = "${entry?.size_bytes}:${entry?.modified_at}"
        val file = File(cacheDir(), sha("$documentId:$stamp"))
        if (!file.isFile || file.length() != entry?.size_bytes) {
            val part = File(cacheDir(), file.name + ".part")
            try {
                hubCall { hub -> hub.download(place.share, place.path, part) }
                if (!part.renameTo(file)) throw FileNotFoundException("The file could not be kept on this phone.")
            } finally {
                part.delete()
            }
            trimCache()
        }
        file.setLastModified(System.currentTimeMillis())
        return ParcelFileDescriptor.open(file, modes)
    }

    /** A file of its own on the phone; when it is closed, it goes to the NAS. */
    private fun openForWriting(place: Place, mode: String, modes: Int): ParcelFileDescriptor {
        val temp = File(cacheDir(), "write-${sha(place.id + System.nanoTime())}")
        val exists = known[place.id] != null || hubCall { hub -> hub.list(place.share, place.parent.path).any { it.name == place.name } }
        if (exists && (mode.contains('a') || (mode.contains('r') && !mode.contains('t')))) {
            hubCall { hub -> hub.download(place.share, place.path, temp) }
        }
        return ParcelFileDescriptor.open(temp, modes, writer) { error ->
            try {
                if (error == null) send(place, temp, exists)
            } catch (e: Exception) {
                Log.w(TAG, "saving ${place.id} to the NAS failed: ${e.message}")
            } finally {
                temp.delete()
            }
        }
    }

    private fun send(place: Place, file: File, replace: Boolean) {
        hubCall { hub ->
            hub.upload(Upload("document", place.share, place.parent.path, place.name), file.length(), file.lastModified(), replace) { offset ->
                FileInputStream(file).also { it.skip(offset) }
            }
        }
        known.remove(place.id)
        changed(place.parent)
    }

    private fun changed(parent: Place) {
        context?.contentResolver?.notifyChange(DocumentsContract.buildChildDocumentsUri(AUTHORITY, parent.id), null)
    }

    // ── Making, renaming, moving, deleting ───────────────────────────────

    override fun createDocument(parentDocumentId: String, mimeType: String, displayName: String): String {
        val parent = place(parentDocumentId)
        if (parent.top || !writable(parent.share)) throw SecurityException("Files cannot be made here.")
        val name = hubCall { hub ->
            val taken = hub.names(parent.share, parent.path)
            unique(displayName.replace('/', '_'), taken).also { free ->
                if (mimeType == Document.MIME_TYPE_DIR) hub.makeFolder(parent.share, parent.path, free)
                else hub.upload(Upload("document", parent.share, parent.path, free), 0, System.currentTimeMillis(), false) { ByteArrayInputStream(ByteArray(0)) }
            }
        }
        changed(parent)
        return parent.child(name).id
    }

    override fun deleteDocument(documentId: String) {
        val place = place(documentId)
        if (place.top || place.path.isEmpty() || !writable(place.share)) throw SecurityException("This cannot be deleted.")
        hubCall { hub -> hub.delete(place.share, place.parent.path, place.name) }
        known.remove(place.id)
        changed(place.parent)
    }

    override fun renameDocument(documentId: String, displayName: String): String? {
        val place = place(documentId)
        if (place.top || place.path.isEmpty() || !writable(place.share)) throw SecurityException("This cannot be renamed.")
        val name = displayName.replace('/', '_')
        hubCall { hub -> hub.rename(place.share, place.parent.path, place.name, name) }
        known.remove(place.id)
        changed(place.parent)
        return place.parent.child(name).id
    }

    override fun moveDocument(sourceDocumentId: String, sourceParentDocumentId: String, targetParentDocumentId: String): String {
        val from = place(sourceDocumentId)
        val target = place(targetParentDocumentId)
        if (target.top || from.share != target.share) throw UnsupportedOperationException("Files move inside one shared folder only.")
        if (!writable(from.share)) throw SecurityException("This shared folder is read only.")
        hubCall { hub -> hub.move(from.share, from.parent.path, from.name, target.path) }
        known.remove(from.id)
        changed(from.parent)
        changed(target)
        return target.child(from.name).id
    }
}
