package org.alvaos.photos

import java.io.ByteArrayInputStream
import java.io.InputStream
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertTrue

/** A NAS that answers like backend/hub_photos_sync.py, kept in memory. */
class FakeHub : HubApi {
    val files = mutableMapOf<String, Long>()          // "album/name" -> size
    val trash = mutableSetOf<String>()
    val manifest = mutableMapOf<String, String>()     // phone id -> "album/name"
    val uploads = mutableListOf<String>()
    var failNext = false
    var failAlways = false

    override fun addPhone(name: String) = Phone("0123456789ab", name, name)

    override fun plan(phoneId: String, items: List<PlanItem>, deleted: Collection<String>, keep: Collection<String>): Plan {
        var trashed = 0
        for (id in deleted) {
            val path = manifest.remove(id) ?: continue
            if (id !in keep && files.remove(path) != null) { trash += path; trashed++ }
        }
        val deleteOnPhone = manifest.filterValues { it !in files && it in trash }.keys.toList()
        val upload = items.filter { it.id !in manifest }.mapNotNull { item ->
            val path = "${item.album}/${item.name}"
            if (files[path] == item.size) { manifest[item.id] = path; null }
            else Upload(item.id, "anna-home", "Photos/Phone/${item.album}", item.name)
        }
        return Plan(upload, deleteOnPhone, trashed)
    }

    override fun upload(target: Upload, size: Long, modified: Long, open: (offset: Long) -> InputStream) {
        if (failNext) { failNext = false; throw java.io.IOException("Connection lost") }
        if (failAlways) throw java.io.IOException("Connection lost")
        val bytes = open(0).readBytes()
        assertEquals(size, bytes.size.toLong())
        files["${target.path.substringAfterLast('/')}/${target.name}"] = size
        uploads += target.name
    }

    override fun done(phoneId: String, items: List<DoneItem>) {
        items.forEach { manifest[it.id] = "${it.path.substringAfterLast('/')}/${it.name}" }
    }

    override fun deleted(phoneId: String, ids: Collection<String>) { ids.forEach { manifest.remove(it) } }
}

class FakeLibrary : Library {
    val photos = mutableListOf<LocalPhoto>()
    override fun albums() = photos.map { it.album }.distinct()
    override fun photos(albums: Set<String>) = photos.filter { it.album in albums }
    override fun open(id: String, offset: Long): InputStream =
        ByteArrayInputStream(ByteArray(photos.first { it.id == id }.size.toInt())).also { it.skip(offset) }
    fun add(id: String, album: String = "Camera", size: Long = 10) = photos.add(LocalPhoto(id, album, "$id.jpg", size, 1000))
}

class MemoryState : SyncState {
    override var phoneId = ""
    override var albums = setOf("Camera")
    override var known = mapOf<String, String>()
    override var pendingDeletes = setOf<String>()
    override var keep = setOf<String>()
    override var deleteOnNas = true
}

class SyncTest {
    private val hub = FakeHub()
    private val library = FakeLibrary()
    private val state = MemoryState()
    private val engine = SyncEngine(hub, library, state)

    @Test
    fun backsUpTheChosenAlbumsOnce() {
        library.add("1"); library.add("2"); library.add("3", album = "WhatsApp Images")
        val first = engine.run()
        assertEquals(2, first.uploaded)
        assertEquals(setOf("1.jpg", "2.jpg"), hub.uploads.toSet())         // WhatsApp is not chosen
        assertEquals(0, engine.run().uploaded)
        assertEquals("0123456789ab", state.phoneId)
    }

    @Test
    fun aFailedUploadIsTriedAgainNextTime() {
        library.add("1")
        hub.failNext = true
        val result = engine.run()
        assertEquals(listOf("1"), result.failed)
        assertEquals("Connection lost", result.lastError)
        assertEquals(1, engine.run().uploaded)
    }

    @Test
    fun deletedHereIsDeletedOnTheNasUnlessTheSettingIsOff() {
        library.add("1"); library.add("2")
        engine.run()
        library.photos.removeIf { it.id == "1" }
        assertEquals(1, engine.run().trashedOnNas)
        assertEquals(setOf("Camera/2.jpg"), hub.files.keys)
        state.deleteOnNas = false
        library.photos.removeIf { it.id == "2" }
        assertEquals(0, engine.run().trashedOnNas)
        assertEquals(setOf("Camera/2.jpg"), hub.files.keys)                // stays on the NAS
    }

    @Test
    fun deletedOnTheNasWaitsForTheYesThenIsReported() {
        library.add("1"); library.add("2")
        engine.run()
        hub.files.remove("Camera/1.jpg"); hub.trash += "Camera/1.jpg"
        assertEquals(setOf("1"), engine.run().toDeleteHere)
        assertEquals(setOf("1"), state.pendingDeletes)
        library.photos.removeIf { it.id == "1" }                            // the person said yes
        engine.deletedHere(listOf("1"))
        assertTrue(state.pendingDeletes.isEmpty())
        assertEquals(0, engine.run().trashedOnNas)
    }

    @Test
    fun freeingUpSpaceKeepsThemOnTheNas() {
        library.add("1"); library.add("2")
        engine.run()
        assertEquals(listOf("1", "2"), engine.backedUp(library.photos).map { it.id })
        library.photos.clear()
        engine.freedUp(listOf("1", "2"))
        assertEquals(0, engine.run().trashedOnNas)
        assertEquals(2, hub.files.size)
        assertTrue(state.keep.isEmpty() && state.known.isEmpty())
    }

    @Test
    fun anAlbumNoLongerChosenIsNotDeleted() {
        library.add("1", album = "Screenshots")
        state.albums = setOf("Camera", "Screenshots")
        engine.run()
        state.albums = setOf("Camera")
        assertEquals(0, engine.run().trashedOnNas)
        assertEquals(setOf("Screenshots/1.jpg"), hub.files.keys)
    }

    @Test
    fun stopsBetweenPicturesAndKeepsWhatIsDone() {
        (1..8).forEach { library.add("$it") }
        var asked = 0
        val first = engine.run(shouldStop = { asked++ >= 3 })
        assertEquals(3, first.uploaded)
        assertTrue(first.stopped)
        assertEquals(5, first.waiting)
        assertEquals(3, state.known.size)                 // saved, so the next run goes on from there
        val next = engine.run()
        assertEquals(5, next.uploaded)
        assertTrue(!next.stopped && next.waiting == 0)
        assertEquals(8, hub.uploads.toSet().size)
    }

    @Test
    fun savesTheProgressEveryFewPictures() {
        (1..7).forEach { library.add("$it") }
        val seen = mutableListOf<Int>()
        engine.run(onProgress = { _, _ -> seen += state.known.size })
        // After 5 uploads the state was saved while the sync was still going.
        assertTrue(seen.any { it == SyncEngine.CHECKPOINT }, seen.toString())
    }

    @Test
    fun givesUpWhenTheNasCannotBeReached() {
        (1..10).forEach { library.add("$it") }
        hub.failAlways = true
        val result = engine.run()
        assertEquals(0, result.uploaded)
        assertEquals(3, result.failed.size)               // three in a row, then it stops trying
        assertEquals(10, result.waiting)
        assertEquals("Connection lost", result.lastError)
    }

    @Test
    fun countsEachAlbumsProgress() {
        library.add("1"); library.add("2"); library.add("3", album = "Screenshots")
        state.albums = setOf("Camera", "Screenshots")
        engine.run()
        library.add("4")
        val here = library.photos(setOf("Camera", "Screenshots"))
        assertEquals(listOf(AlbumProgress("Camera", 3, 2), AlbumProgress("Screenshots", 1, 1)),
            albumProgress(here, state.known, state.albums))
        assertTrue(albumProgress(here, state.known, state.albums).last().done)
    }
}
