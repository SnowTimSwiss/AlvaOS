package org.alvaos.app

import android.content.Context
import android.content.SharedPreferences
import org.alvaos.photos.HubApp
import org.alvaos.photos.HubClient
import org.alvaos.photos.Me
import org.alvaos.photos.StoreTile
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
    /** Every address of the NAS from the QR code (at home); away it is Link. */
    var addresses: List<String>
        get() = JSONArray(prefs.getString("addresses", "[]")).let { a -> List(a.length()) { a.getString(it) } }
            .ifEmpty { listOfNotNull(server.ifEmpty { null }) }
        set(v) = prefs.edit().putString("addresses", JSONArray(v).toString()).apply()
    /** The NAS's AlvaOS Link address (64 hex digits): how to reach it from away. */
    var linkNas: String
        get() = prefs.getString("link_nas", "").orEmpty()
        set(v) = prefs.edit().putString("link_nas", v).apply()
    /** This phone's Link secret key (hex); the NAS knows the phone by it. */
    var linkKey: String
        get() = prefs.getString("link_key", "").orEmpty()
        set(v) = prefs.edit().putString("link_key", v).apply()
    /** Whether the NAS knows this phone's Link key. */
    var linkRegistered: Boolean
        get() = prefs.getBoolean("link_registered", false)
        set(v) = prefs.edit().putBoolean("link_registered", v).apply()
    /** The local port of the Link door, kept so the Hub's cookies stay valid. */
    var linkPort: Int
        get() = prefs.getInt("link_port", 0)
        set(v) = prefs.edit().putInt("link_port", v).apply()
    var nasName: String
        get() = prefs.getString("nas_name", "").orEmpty()
        set(v) = prefs.edit().putString("nas_name", v).apply()
    /** The Hub apps of the person (the app's tabs), as the NAS said last. */
    var hubApps: List<HubApp>
        get() = JSONArray(prefs.getString("hub_apps", "[]")).let { a ->
            List(a.length()) { a.getJSONObject(it) }.map { HubApp(it.optString("id"), it.optString("name"), it.optString("icon")) }
        }
        set(v) = prefs.edit().putString("hub_apps", JSONArray(v.map {
            JSONObject().put("id", it.id).put("name", it.name).put("icon", it.icon)
        }).toString()).apply()
    /** The App Store apps with a tile in the Hub (they open in the browser). */
    var storeApps: List<StoreTile>
        get() = JSONArray(prefs.getString("store_apps", "[]")).let { a ->
            List(a.length()) { a.getJSONObject(it) }.map { StoreTile(it.optString("id"), it.optString("name"), it.optInt("port"), it.optString("path", "/")) }
        }
        set(v) = prefs.edit().putString("store_apps", JSONArray(v.map {
            JSONObject().put("id", it.id).put("name", it.name).put("port", it.port).put("path", it.path)
        }).toString()).apply()

    /** What /api/me said: the apps and the NAS's name. */
    fun remember(me: Me) {
        hubApps = me.hub.apps
        storeApps = me.hub.store
        if (me.nas_name.isNotEmpty()) nasName = me.nas_name
        if (me.user.isNotEmpty()) user = me.user
    }

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
    /** Back up only while the phone charges (the default is: whenever the battery is not low). */
    var chargingOnly: Boolean
        get() = prefs.getBoolean("charging_only", false)
        set(v) = prefs.edit().putBoolean("charging_only", v).apply()
    var lastSync: Long
        get() = prefs.getLong("last_sync", 0)
        set(v) = prefs.edit().putLong("last_sync", v).apply()
    /** When the person was last told that the backup has not worked for days. */
    var lastWarned: Long
        get() = prefs.getLong("last_warned", 0)
        set(v) = prefs.edit().putLong("last_warned", v).apply()
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
        val direct = (listOf(server) + addresses).filter { it.isNotEmpty() && !LinkService.isProxy(it) }.distinct()
        val found = direct.firstOrNull { HubClient(it).reachable() }
            ?: LinkService.proxyUrl(this)?.takeIf { HubClient(it).reachable() }     // away: through AlvaOS Link
            ?: return server
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
