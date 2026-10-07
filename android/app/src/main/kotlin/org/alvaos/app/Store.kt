package org.alvaos.app

import android.content.Context
import android.content.SharedPreferences
import org.alvaos.photos.HubClient
import org.alvaos.photos.SyncState
import org.json.JSONArray
import org.json.JSONObject

/** Everything the app remembers, in its private preferences. */
class Store(context: Context) : SyncState {
    private val prefs: SharedPreferences = context.getSharedPreferences("alvaos", Context.MODE_PRIVATE)

    /** The address that worked last; see `addresses` for all of them. */
    var server: String
        get() = prefs.getString("server", "").orEmpty()
        set(v) = prefs.edit().putString("server", v).apply()
    /** Every address of the NAS from the QR code: at home first, then away (Cloudflare). */
    var addresses: List<String>
        get() = JSONArray(prefs.getString("addresses", "[]")).let { a -> List(a.length()) { a.getString(it) } }
            .ifEmpty { listOfNotNull(server.ifEmpty { null }) }
        set(v) = prefs.edit().putString("addresses", JSONArray(v).toString()).apply()
    var nasName: String
        get() = prefs.getString("nas_name", "").orEmpty()
        set(v) = prefs.edit().putString("nas_name", v).apply()
    /** Whether the person set up the photo backup (it is optional). */
    var backupOn: Boolean
        get() = prefs.getBoolean("backup_on", albums.isNotEmpty())
        set(v) = prefs.edit().putBoolean("backup_on", v).apply()
    var user: String
        get() = prefs.getString("user", "").orEmpty()
        set(v) = prefs.edit().putString("user", v).apply()
    /** The Hub session (cookie), not the password. */
    var token: String
        get() = prefs.getString("token", "").orEmpty()
        set(v) = prefs.edit().putString("token", v).apply()
    var wifiOnly: Boolean
        get() = prefs.getBoolean("wifi_only", true)
        set(v) = prefs.edit().putBoolean("wifi_only", v).apply()
    var lastSync: Long
        get() = prefs.getLong("last_sync", 0)
        set(v) = prefs.edit().putLong("last_sync", v).apply()
    var lastMessage: String
        get() = prefs.getString("last_message", "").orEmpty()
        set(v) = prefs.edit().putString("last_message", v).apply()

    /** The "delete without asking" step was shown once (the person may have said not now). */
    var askedManageMedia: Boolean
        get() = prefs.getBoolean("asked_manage_media", false)
        set(v) = prefs.edit().putBoolean("asked_manage_media", v).apply()

    val signedIn: Boolean get() = server.isNotEmpty() && token.isNotEmpty()

    fun hub() = HubClient(server, token)

    /**
     * The address that answers now: the last one if it still does, else the next
     * (at home the NAS's own address, away the internet one). Not on the main thread.
     */
    fun pickServer(): String {
        val all = (listOf(server) + addresses).filter { it.isNotEmpty() }.distinct()
        val found = all.firstOrNull { HubClient(it).reachable() } ?: return server
        if (found != server) server = found
        return found
    }

    override var phoneId: String
        get() = prefs.getString("phone_id", "").orEmpty()
        set(v) = prefs.edit().putString("phone_id", v).apply()
    override var albums: Set<String>
        get() = prefs.getStringSet("albums", emptySet())!!.toSet()
        set(v) = prefs.edit().putStringSet("albums", v).apply()
    override var known: Map<String, String>
        get() = JSONObject(prefs.getString("known", "{}")!!).let { o -> o.keys().asSequence().associateWith { o.getString(it) } }
        set(v) = prefs.edit().putString("known", JSONObject(v).toString()).apply()
    override var pendingDeletes: Set<String>
        get() = prefs.getStringSet("pending_deletes", emptySet())!!.toSet()
        set(v) = prefs.edit().putStringSet("pending_deletes", v).apply()
    override var keep: Set<String>
        get() = prefs.getStringSet("keep", emptySet())!!.toSet()
        set(v) = prefs.edit().putStringSet("keep", v).apply()
    override var deleteOnNas: Boolean
        get() = prefs.getBoolean("delete_on_nas", true)
        set(v) = prefs.edit().putBoolean("delete_on_nas", v).apply()

    /** Signed out (here or on the NAS): the session goes, the rest stays for signing in again. */
    fun signOut() = prefs.edit().remove("token").apply()
}
