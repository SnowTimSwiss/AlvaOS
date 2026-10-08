package org.alvaos.link

import computer.iroh.BiStream
import computer.iroh.Connection
import computer.iroh.Endpoint
import computer.iroh.EndpointAddr
import computer.iroh.EndpointId
import computer.iroh.EndpointOptions
import computer.iroh.RecvStream
import computer.iroh.SecretKey
import computer.iroh.SendStream
import computer.iroh.presetN0
import computer.iroh.presetN0DisableRelay
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.isActive
import kotlinx.coroutines.joinAll
import kotlinx.coroutines.launch
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.coroutines.withContext
import kotlinx.coroutines.withTimeout
import kotlinx.serialization.encodeToString
import kotlinx.serialization.json.Json
import org.alvaos.photos.DeviceInfo
import org.alvaos.photos.HubException
import org.alvaos.photos.Paired
import java.io.IOException
import java.net.InetAddress
import java.net.ServerSocket
import java.net.Socket

/**
 * AlvaOS Link on the phone: the way to the NAS from anywhere, without a router setting
 * (docs/LINK.md). The NAS has a key pair; its public key, 64 hex digits, is its address. This
 * phone has a key pair of its own, made once and kept (`secretBytes`). iroh connects the two,
 * directly through the routers if possible, else through a relay that only passes on encrypted
 * packets. The NAS lets in only keys it has paired.
 *
 * One QUIC stream per request; its first line names the service (`hub`, `pair`, `ping`).
 */
class LinkClient(secretKey: ByteArray? = null, private val directOnly: Boolean = false) {
    private val key: SecretKey = secretKey?.let { SecretKey.fromBytes(it) } ?: SecretKey.generate()
    private var endpoint: Endpoint? = null
    private val connections = HashMap<String, Connection>()
    private val lock = Mutex()

    /** This phone's private key, to keep: the NAS knows the phone by it. */
    fun secretBytes(): ByteArray = key.toBytes()

    /** This phone's public key, 64 hex digits (what the NAS lists as a phone). */
    fun id(): String = key.public().toBytes().toHex()

    suspend fun start() {
        if (endpoint != null) return
        endpoint = Endpoint.bind(EndpointOptions(
            preset = if (directOnly) presetN0DisableRelay() else presetN0(),
            bindAddr = if (directOnly) "127.0.0.1:0" else null,
            secretKey = key.toBytes(), alpns = emptyList(),
        ))
    }

    suspend fun close() {
        lock.withLock {
            connections.values.forEach { runCatching { it.close(0, "bye".toByteArray()) } }
            connections.clear()
        }
        endpoint?.let { runCatching { it.close() } }
        endpoint = null
    }

    private suspend fun connection(nas: String, hints: List<String>, fresh: Boolean): Connection = lock.withLock {
        connections[nas]?.takeIf { !fresh }?.let { return@withLock it }
        val ep = endpoint ?: throw IOException("Link is not started")
        val addr = EndpointAddr(EndpointId.fromBytes(nas.hexBytes()), null, hints)
        val conn = withTimeout(30_000) { ep.connect(addr, ALPN.toByteArray()) }
        connections[nas] = conn
        conn
    }

    /** A stream to a service of the NAS; its first line is already sent. */
    suspend fun open(nas: String, service: String, hints: List<String> = emptyList()): BiStream {
        var last: Exception? = null
        for (attempt in 0..1) {
            try {
                val bi = connection(nas, hints, fresh = attempt == 1).openBi()
                bi.send().writeAll("$service\n".toByteArray())
                return bi
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                last = e
                lock.withLock { connections.remove(nas) }          // stale: connect again
            }
        }
        throw IOException("The NAS cannot be reached: ${last?.message}")
    }

    /** Trades the pairing code of the Hub (the QR code) for a session of this phone's own. */
    suspend fun pairDevice(nas: String, code: String, device: DeviceInfo, hints: List<String> = emptyList()): Paired {
        val json = Json { ignoreUnknownKeys = true; encodeDefaults = true }
        val request = json.encodeToString(PairRequest(code = code, device = device))
        val bi = open(nas, "pair", hints)
        bi.send().writeAll("$request\n".toByteArray())
        bi.send().finish()
        val answer = String(readAll(bi.recv(), 256 * 1024)).substringBefore('\n')
        val error = Regex("\"error\"\\s*:\\s*\"((?:[^\"\\\\]|\\\\.)*)\"").find(answer)?.groupValues?.get(1)
        if (error != null) throw HubException(error.replace("\\\"", "\"").replace("\\n", "\n"), 401)
        return json.decodeFromString<Paired>(answer)
    }

    /** Whether the NAS answers, and how fast (ms). */
    suspend fun ping(nas: String, hints: List<String> = emptyList()): Long {
        val started = System.nanoTime()
        val bi = open(nas, "ping", hints)
        bi.send().finish()
        val answer = String(readAll(bi.recv(), 16)).trim()
        if (answer != "pong") throw IOException("The NAS did not answer")
        return (System.nanoTime() - started) / 1_000_000
    }

    companion object {
        const val ALPN = "alvaos/link/1"
        private const val CHUNK = 65536u

        suspend fun readAll(recv: RecvStream, limit: Int): ByteArray {
            val out = java.io.ByteArrayOutputStream()
            while (out.size() <= limit) {
                val data = recv.read(CHUNK)
                if (data.isEmpty()) break
                out.write(data)
            }
            return out.toByteArray()
        }

        /** Both ways at once, until both sides are done. */
        suspend fun pipe(socket: Socket, recv: RecvStream, send: SendStream) = withContext(Dispatchers.IO) {
            val up = launch {
                try {
                    val input = socket.getInputStream()
                    val buffer = ByteArray(CHUNK.toInt())
                    while (isActive) {
                        val n = input.read(buffer)
                        if (n < 0) break
                        send.writeAll(buffer.copyOf(n))
                    }
                    send.finish()
                } catch (e: Exception) {
                    runCatching { send.reset(4uL) }
                }
            }
            val down = launch {
                try {
                    val output = socket.getOutputStream()
                    while (isActive) {
                        val data = recv.read(CHUNK)
                        if (data.isEmpty()) break
                        output.write(data)
                        output.flush()
                    }
                    runCatching { socket.shutdownOutput() }
                } catch (e: Exception) {
                    runCatching { socket.close() }
                }
            }
            joinAll(up, down)
            runCatching { socket.close() }
        }
    }

    @kotlinx.serialization.Serializable
    private data class PairRequest(val op: String = "device", val code: String, val device: DeviceInfo)
}

/**
 * The Hub of the NAS as a web address on this phone: `http://127.0.0.1:<port>`. Every connection to it
 * is carried through Link to the Hub. The WebView and the sync use it like any other address of the
 * NAS; the port is kept between starts so the Hub's cookies stay valid.
 */
class LinkProxy(private val link: LinkClient, private val nas: String, private val hints: List<String> = emptyList()) {
    private var server: ServerSocket? = null
    private var job: Job? = null

    val port: Int get() = server?.localPort ?: 0
    val baseUrl: String get() = "http://127.0.0.1:$port"

    /** Starts listening; `preferred` is tried first (0 or taken: any free port). */
    fun start(scope: CoroutineScope, preferred: Int = 0): String {
        if (server != null) return baseUrl
        val loopback = InetAddress.getByName("127.0.0.1")
        val s = try { ServerSocket(preferred, 64, loopback) } catch (e: IOException) { ServerSocket(0, 64, loopback) }
        server = s
        job = scope.launch(Dispatchers.IO) {
            while (isActive) {
                val socket = try { s.accept() } catch (e: IOException) { break }
                launch {
                    try {
                        val bi = link.open(nas, "hub", hints)
                        LinkClient.pipe(socket, bi.recv(), bi.send())
                    } catch (e: CancellationException) {
                        socket.close()
                        throw e
                    } catch (e: Exception) {
                        runCatching { socket.close() }
                    }
                }
            }
        }
        return baseUrl
    }

    fun stop() {
        job?.cancel()
        runCatching { server?.close() }
        server = null
    }
}

fun ByteArray.toHex(): String = joinToString("") { "%02x".format(it) }

fun String.hexBytes(): ByteArray {
    require(length == 64 && all { it in "0123456789abcdef" }) { "A Link address is 64 hex digits" }
    return ByteArray(32) { substring(it * 2, it * 2 + 2).toInt(16).toByte() }
}
