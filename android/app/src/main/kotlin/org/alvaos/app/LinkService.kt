package org.alvaos.app

import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.runBlocking
import org.alvaos.link.LinkClient
import org.alvaos.link.LinkProxy
import org.alvaos.link.hexBytes
import org.alvaos.link.toHex
import org.alvaos.photos.DeviceInfo
import org.alvaos.photos.HubClient
import org.alvaos.photos.Paired

/**
 * The way to the NAS from away: AlvaOS Link (iroh). A local address on the phone
 * (127.0.0.1) leads through it to the Hub, so the WebView and the sync use it like
 * any other address. When the build has no Link library, everything here says "not
 * available" and the app works at home only.
 */
object LinkService {
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
    private var client: LinkClient? = null
    private var proxy: LinkProxy? = null
    private var proxyNas = ""

    /** Why Link cannot be used in this build (the library is missing), or "". */
    @Volatile var unavailable: String = ""
        private set

    val available: Boolean get() = unavailable.isEmpty()

    /** Whether an address is the local door into Link. */
    fun isProxy(address: String) = address.startsWith("http://127.0.0.1:")

    @Synchronized
    private fun client(store: Store): LinkClient {
        client?.let { return it }
        val saved = store.linkKey.takeIf { it.length == 64 }?.hexBytes()
        val made = LinkClient(saved)
        if (saved == null) store.linkKey = made.secretBytes().toHex()
        runBlocking { made.start() }
        client = made
        return made
    }

    /** This phone's Link address (public key, 64 hex digits), or "" if Link is not available. */
    fun id(store: Store): String = guarded { client(store).id() } ?: ""

    /** The local address that leads to the NAS through Link, or null (no Link NAS, or no library). */
    @Synchronized
    fun proxyUrl(store: Store): String? = guarded {
        val nas = store.linkNas
        if (nas.length != 64) return@guarded null
        val c = client(store)
        proxy?.takeIf { proxyNas == nas }?.let { return@guarded it.baseUrl }
        proxy?.stop()
        val p = LinkProxy(c, nas)
        val url = p.start(scope, store.linkPort)
        store.linkPort = p.port
        proxy = p
        proxyNas = nas
        url
    }

    /** Pairs through Link with the code of the QR (no address needed), and keeps the NAS's Link address. */
    suspend fun pair(store: Store, nas: String, code: String, device: DeviceInfo): Paired {
        val c = try { client(store) } catch (e: Throwable) { fail(e); throw java.io.IOException(NOT_AVAILABLE) }
        val paired = c.pairDevice(nas, code, device)
        store.linkNas = nas
        store.linkRegistered = true            // the NAS learned the key by this very pairing
        return paired
    }

    /** After pairing at home: tell the NAS this phone's key, so it may come in from away too. */
    fun register(store: Store, hub: HubClient) {
        if (store.linkNas.length != 64) return
        if (store.linkRegistered) return
        val key = id(store).ifEmpty { return }
        try { hub.registerLink(key); store.linkRegistered = true } catch (e: Exception) { /* at home it works anyway; tried again on the next start */ }
    }

    /** One line for the settings: how this phone gets to the NAS from away. */
    fun describe(store: Store): String = when {
        store.linkNas.length != 64 -> "Not set up: show a new QR code in the Hub once, with Link on."
        !available -> NOT_AVAILABLE
        else -> "On: the phone finds ${store.nasName.ifEmpty { "the NAS" }} without any router setting."
    }

    @Synchronized
    fun reset() {
        proxy?.stop(); proxy = null; proxyNas = ""
        client = null
    }

    private inline fun <T> guarded(block: () -> T): T? = try {
        block()
    } catch (e: Throwable) {          // UnsatisfiedLinkError / NoClassDefFoundError: the library is not in this build
        fail(e)
        null
    }

    private fun fail(e: Throwable) {
        if (e is UnsatisfiedLinkError || e is NoClassDefFoundError || e is ExceptionInInitializerError) unavailable = NOT_AVAILABLE
    }

    const val NOT_AVAILABLE = "AlvaOS Link is not available in this build of the app."
}
