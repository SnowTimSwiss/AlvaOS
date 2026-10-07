package org.alvaos.photos

import java.io.InputStream

/** A picture or video on the phone. `modified` in milliseconds. */
data class LocalPhoto(val id: String, val album: String, val name: String, val size: Long, val modified: Long)

/** The phone's gallery (on Android: MediaStore). */
interface Library {
    /** Albums with something in them, by name (Camera, Screenshots, WhatsApp Images, …). */
    fun albums(): List<String>
    fun photos(albums: Set<String>): List<LocalPhoto>
    fun open(id: String, offset: Long): InputStream
}

/** What the app remembers between syncs (on Android: its preferences). */
interface SyncState {
    var phoneId: String
    /** Albums the person chose to back up. */
    var albums: Set<String>
    /** What is backed up: phone id -> album. */
    var known: Map<String, String>
    /** Deleted on the NAS, waiting for the person's yes to delete it here too. */
    var pendingDeletes: Set<String>
    /** Freed up on purpose: deleted here, stays on the NAS. */
    var keep: Set<String>
    /** Deleting here deletes on the NAS too (into its trash). */
    var deleteOnNas: Boolean
}

data class SyncResult(
    val uploaded: Int,
    val failed: List<String>,
    val trashedOnNas: Int,
    val toDeleteHere: Set<String>,
    val lastError: String = "",
)

/**
 * One sync, the same every time (backend/hub_photos_sync.py does the deciding):
 *  1. what vanished from a chosen album since last time was deleted here;
 *  2. ask the NAS for the plan with what is here now;
 *  3. upload what it asks for and report it;
 *  4. what it says was deleted on the NAS waits for the person's yes.
 */
class SyncEngine(private val hub: HubApi, private val library: Library, private val state: SyncState) {

    fun run(phoneName: () -> String = { "Phone" }, onProgress: (done: Int, total: Int) -> Unit = { _, _ -> }): SyncResult {
        if (state.phoneId.isEmpty()) state.phoneId = hub.addPhone(phoneName()).id
        val chosen = state.albums
        val here = library.photos(chosen)
        val hereIds = here.map { it.id }.toSet()
        // An album no longer chosen is not "deleted": its pictures stay on both sides.
        val known = state.known.filterValues { it in chosen }
        val vanished = known.keys - hereIds
        val keep = state.keep.intersect(vanished) + (if (state.deleteOnNas) emptySet() else vanished)
        val plan = hub.plan(state.phoneId, here.map { PlanItem(it.id, it.album, it.name, it.size, it.modified) },
            vanished, keep)

        val byId = here.associateBy { it.id }
        val uploading = plan.upload.map { it.id }.toSet()
        val nowKnown = state.known.toMutableMap()
        vanished.forEach { nowKnown.remove(it) }
        // Everything here the NAS did not ask for is there already.
        here.filter { it.id !in uploading }.forEach { nowKnown[it.id] = it.album }

        val failed = mutableListOf<String>()
        var lastError = ""
        val done = mutableListOf<DoneItem>()
        var uploaded = 0
        plan.upload.forEachIndexed { i, target ->
            onProgress(i, plan.upload.size)
            val photo = byId[target.id] ?: return@forEachIndexed
            try {
                hub.upload(target, photo.size, photo.modified) { offset -> library.open(photo.id, offset) }
                done += DoneItem(photo.id, target.path, target.name, photo.size, photo.modified)
                nowKnown[photo.id] = photo.album
                uploaded += 1
                if (done.size >= 50) {
                    hub.done(state.phoneId, done.toList())
                    done.clear()
                    state.known = nowKnown.toMap()
                }
            } catch (e: HubException) {
                if (e.signedOut) throw e
                failed += photo.id
                lastError = e.message ?: ""
            } catch (e: java.io.IOException) {
                failed += photo.id
                lastError = e.message ?: ""
            }
        }
        if (done.isNotEmpty()) hub.done(state.phoneId, done)
        onProgress(plan.upload.size, plan.upload.size)

        state.known = nowKnown.toMap()
        state.keep = state.keep - vanished
        // Only what is still here and in a chosen album; the person says yes first.
        state.pendingDeletes = (state.pendingDeletes + plan.delete_on_phone).intersect(hereIds)
        return SyncResult(uploaded, failed, plan.trashed, state.pendingDeletes, lastError)
    }

    /** The person said yes and the phone deleted them: tell the NAS. */
    fun deletedHere(ids: Collection<String>) {
        if (ids.isEmpty()) return
        hub.deleted(state.phoneId, ids)
        state.known = state.known - ids.toSet()
        state.pendingDeletes = state.pendingDeletes - ids.toSet()
    }

    /** "Free up space": what may go from the phone and stays on the NAS. */
    fun backedUp(here: List<LocalPhoto>): List<LocalPhoto> =
        here.filter { it.id in state.known && it.id !in state.pendingDeletes }

    /** After the phone deleted them for "Free up space". */
    fun freedUp(ids: Collection<String>) {
        state.keep = state.keep + ids
    }
}
