package org.alvaos.photos

import kotlinx.serialization.Serializable
import kotlinx.serialization.encodeToString
import kotlinx.serialization.json.Json
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody
import okhttp3.RequestBody.Companion.toRequestBody
import okhttp3.Response
import okio.BufferedSink
import java.io.IOException
import java.io.InputStream
import java.util.concurrent.TimeUnit

/** What went wrong, in a sentence for the person (the NAS writes them). */
class HubException(message: String, val status: Int = 0) : IOException(message) {
    /** The sign-in ended (password changed, signed out): sign in again. */
    val signedOut: Boolean get() = status == 401
}

@Serializable
data class Phone(val id: String, val name: String = "", val folder: String = "")

@Serializable
data class Upload(val id: String, val share: String, val path: String, val name: String)

@Serializable
data class Plan(
    val upload: List<Upload> = emptyList(),
    val delete_on_phone: List<String> = emptyList(),
    val trashed: Int = 0,
    val forgotten: Int = 0,
)

/** One picture or video on the phone, as the NAS sees it. */
@Serializable
data class PlanItem(val id: String, val album: String, val name: String, val size: Long, val modified: Long)

@Serializable
data class DoneItem(val id: String, val path: String, val name: String, val size: Long, val modified: Long)

/** What the app says about the phone; the Hub lists it under Phones and devices. */
@Serializable
data class DeviceInfo(val name: String, val model: String = "", val platform: String = "android", val app_version: String = "")

/** An app of the Hub (Files, Photos, Calendar, Chat), as /api/me lists it. */
@Serializable
data class HubApp(val id: String, val name: String = "", val icon: String = "")

/** An app from the App Store with a tile in the Hub (Jellyfin, Immich, …): its own port. */
@Serializable
data class StoreTile(val id: String = "", val name: String = "", val port: Int = 0, val path: String = "/")

@Serializable
data class HubInfo(val apps: List<HubApp> = emptyList(), val store: List<StoreTile> = emptyList())

/** Who is signed in, on which NAS, with which apps. */
@Serializable
data class Me(val user: String = "", val nas_name: String = "", val hub: HubInfo = HubInfo(), val public_url: String = "")

/** A session of the app's own, from a QR code or a password. */
@Serializable
data class Paired(val token: String, val user: String = "", val nas_name: String = "", val device: String = "")

/**
 * What the QR code in the Hub › Phones and devices says:
 * `alvaos://pair?c=CODE&n=<NAS>&u=<person>&a=<address>&a=<address>`.
 * The addresses: the one the browser used, then the internet one if there is.
 */
data class PairLink(val code: String, val addresses: List<String>, val nas: String, val user: String) {
    companion object {
        fun parse(text: String): PairLink? {
            val t = text.trim()
            if (!t.startsWith("alvaos://pair?")) return null
            val params = t.substringAfter('?').split('&').mapNotNull { part ->
                val key = part.substringBefore('=', "")
                if (key.isEmpty()) null
                else key to java.net.URLDecoder.decode(part.substringAfter('='), "UTF-8")
            }
            val code = params.firstOrNull { it.first == "c" }?.second.orEmpty()
            val addresses = params.filter { it.first == "a" }.map { it.second.trimEnd('/') }
                .filter { it.startsWith("http://") || it.startsWith("https://") }
            if (code.length < 8 || addresses.isEmpty()) return null
            return PairLink(code, addresses, params.firstOrNull { it.first == "n" }?.second.orEmpty(),
                params.firstOrNull { it.first == "u" }?.second.orEmpty())
        }
    }
}

/** The calls of the Hub (backend/hub_photos_sync.py, files_server.py). */
interface HubApi {
    fun addPhone(name: String): Phone
    fun plan(phoneId: String, items: List<PlanItem>, deleted: Collection<String>, keep: Collection<String>): Plan
    fun upload(target: Upload, size: Long, modified: Long, open: (offset: Long) -> InputStream)
    fun done(phoneId: String, items: List<DoneItem>)
    fun deleted(phoneId: String, ids: Collection<String>)
}

/**
 * The Hub of a NAS: the same address and sign-in as in the browser (port 8090,
 * HTTPS 9443, or through Tailscale or a Cloudflare Tunnel). The session is the
 * `alvaos_files` cookie; every change carries `X-AlvaOS-Files: 1`.
 */
class HubClient(
    baseUrl: String,
    var token: String = "",
    private val http: OkHttpClient = defaultHttp(),
) : HubApi {
    private val base = baseUrl.trimEnd('/')
    private val json = Json { ignoreUnknownKeys = true; encodeDefaults = true }

    companion object {
        const val COOKIE = "alvaos_files"
        const val PIECE = 16L * 1024 * 1024
        private val JSON_TYPE = "application/json".toMediaType()
        private val BYTES = "application/octet-stream".toMediaType()

        fun defaultHttp(): OkHttpClient = OkHttpClient.Builder()
            .connectTimeout(20, TimeUnit.SECONDS)
            .readTimeout(120, TimeUnit.SECONDS)
            .writeTimeout(300, TimeUnit.SECONDS)
            .build()
    }

    private fun request(path: String): Request.Builder = Request.Builder().url(base + path)
        .header("X-AlvaOS-Files", "1")
        .apply { if (token.isNotEmpty()) header("Cookie", "$COOKIE=$token") }

    private fun send(builder: Request.Builder): String = http.newCall(builder.build()).execute().use { res ->
        val body = res.body?.string().orEmpty()
        if (!res.isSuccessful) throw HubException(errorOf(body) ?: "The NAS answered ${res.code}.", res.code)
        body
    }

    private fun errorOf(body: String): String? = try {
        json.decodeFromString<Map<String, kotlinx.serialization.json.JsonElement>>(body)["error"]
            ?.toString()?.trim('"')
    } catch (e: Exception) {
        null
    }

    private inline fun <reified T> post(path: String, body: T): String =
        send(request(path).post(json.encodeToString(body).toRequestBody(JSON_TYPE)))

    @Serializable
    private data class LoginBody(val username: String, val password: String, val code: String, val device: DeviceInfo?)

    @Serializable
    private data class LoginAnswer(val token: String = "")

    /**
     * Signs in with the name and password of the shared folders; keeps and returns
     * the session. With `device` it is a session of the app's own (listed in the Hub).
     */
    fun signIn(user: String, password: String, code: String = "", device: DeviceInfo? = null): String {
        val res: Response = http.newCall(request("/api/login")
            .post(json.encodeToString(LoginBody(user, password, code, device))
                .toRequestBody(JSON_TYPE)).build()).execute()
        res.use {
            val body = it.body?.string().orEmpty()
            if (!it.isSuccessful) {
                if (body.contains("needs_code") && body.contains("true") && code.isEmpty()) {
                    throw HubException("Enter the two-step code too.", it.code)
                }
                throw HubException(errorOf(body) ?: "Signing in did not work (${it.code}).", it.code)
            }
            val own = try { json.decodeFromString<LoginAnswer>(body).token } catch (e: Exception) { "" }
            token = own.ifEmpty {
                it.headers("Set-Cookie").firstOrNull { c -> c.startsWith("$COOKIE=") }
                    ?.substringAfter("=")?.substringBefore(";")
                    ?: throw HubException("The NAS did not keep the sign-in.")
            }
            return token
        }
    }

    @Serializable
    private data class PairBody(val code: String, val device: DeviceInfo)

    /** Trades the code of a QR code (or typed) for a session of the app's own. */
    fun pair(code: String, device: DeviceInfo): Paired {
        val paired = json.decodeFromString<Paired>(post("/api/devices/pair", PairBody(code, device)))
        token = paired.token
        return paired
    }

    /** Ends this phone's session on the NAS (it leaves the Hub's device list). */
    fun signOut() {
        try { post("/api/logout", emptyMap<String, String>()) } catch (e: IOException) { /* signed out here anyway */ }
    }

    /** Whether the Hub answers at this address, quickly (to pick home or away). */
    fun reachable(): Boolean = try {
        http.newBuilder().connectTimeout(4, TimeUnit.SECONDS).readTimeout(6, TimeUnit.SECONDS).build()
            .newCall(Request.Builder().url("$base/api/me").build()).execute().use { it.code == 200 || it.code == 401 }
    } catch (e: Exception) {
        false
    }

    /** Who is signed in and which Hub apps they have; HubException(401) when signed out. */
    fun me(): Me = json.decodeFromString(send(request("/api/me")))

    /** Whether this session still works: false only when the NAS says signed out (401). */
    fun signedIn(): Boolean = http.newCall(request("/api/me").build()).execute().use { it.code != 401 }

    @Serializable
    private data class Added(val phone: Phone)

    override fun addPhone(name: String): Phone =
        json.decodeFromString<Added>(post("/api/photos/phones", mapOf("name" to name))).phone

    @Serializable
    private data class PlanBody(val items: List<PlanItem>, val deleted: List<String>, val keep: List<String>)

    override fun plan(phoneId: String, items: List<PlanItem>, deleted: Collection<String>, keep: Collection<String>): Plan =
        json.decodeFromString(post("/api/photos/phones/$phoneId/plan", PlanBody(items, deleted.toList(), keep.toList())))

    @Serializable
    private data class DoneBody(val items: List<DoneItem>)

    override fun done(phoneId: String, items: List<DoneItem>) {
        post("/api/photos/phones/$phoneId/done", DoneBody(items))
    }

    override fun deleted(phoneId: String, ids: Collection<String>) {
        post("/api/photos/phones/$phoneId/deleted", mapOf("ids" to ids.toList()))
    }

    private fun query(target: Upload, extra: String = ""): String =
        "share=${enc(target.share)}&path=${enc(target.path)}&name=${enc(target.name)}$extra"

    private fun enc(s: String) = java.net.URLEncoder.encode(s, "UTF-8").replace("+", "%20")

    @Serializable
    private data class Sized(val size: Long = 0)

    /**
     * The resumable upload of Files: pieces of 16 MB; when the connection drops,
     * the NAS says how much it has and it goes on from there. `open(offset)`
     * gives the file from that byte on.
     */
    override fun upload(target: Upload, size: Long, modified: Long, open: (offset: Long) -> InputStream) {
        post("/api/upload/abort", mapOf("share" to target.share, "path" to target.path, "name" to target.name))
        var offset = 0L
        var first = true
        var tries = 0
        while (first || offset < size) {
            try {
                val length = minOf(PIECE, size - offset)
                val body = object : RequestBody() {
                    override fun contentType() = BYTES
                    override fun contentLength() = length
                    override fun writeTo(sink: BufferedSink) {
                        open(offset).use { input ->
                            val buffer = ByteArray(64 * 1024)
                            var left = length
                            while (left > 0) {
                                val n = input.read(buffer, 0, minOf(buffer.size.toLong(), left).toInt())
                                if (n < 0) throw IOException("The file got shorter while it was uploaded.")
                                sink.write(buffer, 0, n)
                                left -= n
                            }
                        }
                    }
                }
                val answer = send(request("/api/upload/piece?" + query(target, "&offset=$offset")).post(body))
                offset = json.decodeFromString<Sized>(answer).size
                first = false
                tries = 0
            } catch (e: IOException) {
                if (e is HubException && e.status in 400..499) throw e
                tries += 1
                if (tries > 5) throw e
                Thread.sleep(minOf(30_000L, 1500L shl tries))
                offset = json.decodeFromString<Sized>(send(request("/api/upload/status?" + query(target)).get())).size
                first = false
            }
        }
        @Serializable
        data class Finish(val share: String, val path: String, val name: String, val size: Long, val modified: Long)
        post("/api/upload/finish", Finish(target.share, target.path, target.name, size, modified))
    }
}
