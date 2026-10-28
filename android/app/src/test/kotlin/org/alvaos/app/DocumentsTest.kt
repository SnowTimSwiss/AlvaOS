package org.alvaos.app

import android.content.Context
import android.content.pm.ProviderInfo
import android.database.Cursor
import android.provider.DocumentsContract.Document
import android.provider.DocumentsContract.Root
import androidx.test.core.app.ApplicationProvider
import androidx.test.ext.junit.runners.AndroidJUnit4
import okhttp3.mockwebserver.Dispatcher
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import okhttp3.mockwebserver.RecordedRequest
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.Robolectric
import org.robolectric.annotation.Config

/** AlvaOS in the system's Files app: the roots, the folders, a file read and a file made, against a pretend NAS. */
@RunWith(AndroidJUnit4::class)
@Config(sdk = [34])
class DocumentsTest {
    private val ctx: Context = ApplicationProvider.getApplicationContext()
    private val nas = MockWebServer()
    private val seen = mutableListOf<String>()
    private lateinit var provider: AlvaDocuments

    private fun json(body: String) = MockResponse().setHeader("Content-Type", "application/json").setBody(body)

    @Before
    fun setUp() {
        nas.dispatcher = object : Dispatcher() {
            override fun dispatch(request: RecordedRequest): MockResponse {
                val path = request.path.orEmpty()
                seen += "${request.method} $path"
                return when {
                    path.startsWith("/api/me") -> json("""{"user": "tim", "nas_name": "cygnus", "shares": [
                        {"name": "Family", "access": "write"}, {"name": "Music", "access": "read"}]}""")
                    path.startsWith("/api/list?share=Family&path=Docs") -> json("""{"entries": []}""")
                    path.startsWith("/api/list?share=Family") -> json("""{"access": "write", "entries": [
                        {"name": "Docs", "type": "folder", "size_bytes": 0, "modified_at": "2026-10-28T12:00:00+00:00"},
                        {"name": "a.txt", "type": "file", "size_bytes": 5, "modified_at": "2026-10-28T12:00:00+00:00"}]}""")
                    path.startsWith("/api/link") -> json("""{"url": "/api/get/tok"}""")
                    path.startsWith("/api/get/tok") -> MockResponse().setBody("hello")
                    path.startsWith("/api/upload/piece") -> json("""{"size": 0}""")
                    path.startsWith("/api/upload") || path.startsWith("/api/mkdir") -> json("{}")
                    else -> MockResponse().setResponseCode(404)
                }
            }
        }
        nas.start()
        Store(ctx).apply {
            server = nas.url("/").toString().trimEnd('/')
            addresses = listOf(server)
            token = "test"
            user = "tim"
            nasName = "cygnus"
        }
        val info = ProviderInfo().apply {
            authority = AlvaDocuments.AUTHORITY
            exported = true
            grantUriPermissions = true
            readPermission = android.Manifest.permission.MANAGE_DOCUMENTS
            writePermission = android.Manifest.permission.MANAGE_DOCUMENTS
        }
        provider = Robolectric.buildContentProvider(AlvaDocuments::class.java).create(info).get()
    }

    @After
    fun tearDown() = nas.shutdown()

    private fun rows(c: Cursor): List<Map<String, String?>> = buildList {
        while (c.moveToNext()) add(c.columnNames.associateWith { c.getString(c.getColumnIndexOrThrow(it)) })
    }

    @Test
    fun theFilesAppShowsOneEntryNamedAfterTheNas() {
        val root = rows(provider.queryRoots(null)).single()
        assertEquals("AlvaOS – cygnus", root[Root.COLUMN_TITLE])
        assertEquals("/", root[Root.COLUMN_DOCUMENT_ID])
        Store(ctx).signOut()
        assertTrue("signed out: no entry", rows(provider.queryRoots(null)).isEmpty())
    }

    @Test
    fun theSharesAndTheirFoldersAreListedAndReadOnlyOnesCannotBeChanged() {
        val shares = rows(provider.queryChildDocuments("/", null, null as String?))
        assertEquals(listOf("Family", "Music"), shares.map { it[Document.COLUMN_DISPLAY_NAME] })
        val family = shares[0][Document.COLUMN_FLAGS]!!.toInt()
        val music = shares[1][Document.COLUMN_FLAGS]!!.toInt()
        assertTrue(family and Document.FLAG_DIR_SUPPORTS_CREATE != 0)
        assertFalse(music and Document.FLAG_DIR_SUPPORTS_CREATE != 0)
        val inside = rows(provider.queryChildDocuments("/Family", null, null as String?))
        assertEquals(listOf("/Family/Docs", "/Family/a.txt"), inside.map { it[Document.COLUMN_DOCUMENT_ID] })
        assertEquals(Document.MIME_TYPE_DIR, inside[0][Document.COLUMN_MIME_TYPE])
        assertEquals("text/plain", inside[1][Document.COLUMN_MIME_TYPE])
        assertEquals("5", inside[1][Document.COLUMN_SIZE])
        assertTrue(inside[1][Document.COLUMN_FLAGS]!!.toInt() and Document.FLAG_SUPPORTS_WRITE != 0)
    }

    @Test
    fun aFileIsReadThroughItsLinkAndKeptForNextTime() {
        provider.queryChildDocuments("/Family", null, null as String?)
        val text = { provider.openDocument("/Family/a.txt", "r", null).let { fd -> java.io.FileInputStream(fd.fileDescriptor).use { it.readBytes().decodeToString() } } }
        assertEquals("hello", text())
        assertEquals("hello", text())
        assertEquals("downloaded once", 1, seen.count { it.startsWith("GET /api/get/tok") })
    }

    @Test
    fun aNewFileTakesAFreeNameAndAFolderIsMade() {
        val id = provider.createDocument("/Family", "text/plain", "a.txt")
        assertEquals("/Family/a (1).txt", id)
        assertTrue(seen.any { it.startsWith("POST /api/upload/finish") })
        assertEquals("/Family/Photos", provider.createDocument("/Family", Document.MIME_TYPE_DIR, "Photos"))
        assertTrue(seen.any { it.startsWith("POST /api/mkdir") })
        assertTrue(runCatching { provider.createDocument("/Music", "text/plain", "x.txt") }.exceptionOrNull() is SecurityException)
    }

    @Test
    fun placesAndNamesWorkOut() {
        assertEquals(AlvaDocuments.Companion.Place("Family", "Docs/x.txt"), AlvaDocuments.place("/Family/Docs/x.txt"))
        assertEquals("/Family/Docs", AlvaDocuments.place("/Family/Docs/x.txt").parent.id)
        assertEquals("/", AlvaDocuments.place("/Family").parent.id)
        assertEquals("a (2).txt", AlvaDocuments.unique("a.txt", setOf("a.txt", "a (1).txt")))
        assertEquals("a.txt", AlvaDocuments.unique("a.txt", emptySet()))
        assertTrue(provider.isChildDocument("/Family", "/Family/Docs/x.txt"))
        assertFalse(provider.isChildDocument("/Family", "/FamilyOther/x"))
    }
}
