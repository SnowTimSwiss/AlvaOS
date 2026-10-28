package org.alvaos.link

import com.sun.net.httpserver.HttpServer
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.runBlocking
import okhttp3.OkHttpClient
import okhttp3.Request
import org.alvaos.photos.DeviceInfo
import org.alvaos.photos.HubException
import org.junit.Assume.assumeTrue
import java.io.BufferedReader
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import java.io.File
import java.net.InetSocketAddress
import java.nio.file.Files
import java.util.concurrent.TimeUnit
import kotlin.test.AfterTest
import kotlin.test.BeforeTest
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFailsWith
import kotlin.test.assertTrue

/**
 * The phone's Link client against the real daemon of the NAS (backend/link_daemon.py), the Hub stood in for
 * by a small web server. Needs python3 with `pip install iroh`; skipped where there is none.
 */
class LinkTest {
    private lateinit var hub: HttpServer
    private var daemon: Process? = null
    private var daemonLog: File? = null
    private lateinit var nasId: String
    private lateinit var hints: List<String>
    private val seen = mutableListOf<String>()
    private lateinit var embedPorts: String

    private fun backend(): File = generateSequence(File("").absoluteFile) { it.parentFile }
        .map { File(it, "backend/link_daemon.py") }.first { it.exists() }

    @BeforeTest
    fun start() {
        val ok = try {
            ProcessBuilder("python3", "-c", "import iroh").redirectErrorStream(true).start().waitFor(30, TimeUnit.SECONDS)
                .let { true } && ProcessBuilder("python3", "-c", "import iroh").start().apply { waitFor(30, TimeUnit.SECONDS) }.exitValue() == 0
        } catch (e: Exception) {
            false
        }
        assumeTrue("python3 with iroh is needed", ok)
        hub = HttpServer.create(InetSocketAddress("127.0.0.1", 0), 0)
        hub.createContext("/") { x ->
            val body = when {
                x.requestURI.path == "/api/devices/pair" && x.requestMethod == "POST" -> {
                    val sent = x.requestBody.readBytes().decodeToString()
                    seen += "pair ${x.remoteAddress.address.hostAddress} $sent"
                    if (sent.contains("ABCD1234")) """{"success":true,"user":"anna","token":"tok","device":"dev1","nas_name":"cygnus"}"""
                    else """{"error":"This code is not valid"}"""
                }
                else -> """{"hello":"from the hub","path":"${x.requestURI.path}","you":"${x.remoteAddress.address.hostAddress}"}"""
            }
            x.sendResponseHeaders(200, body.toByteArray().size.toLong())
            x.responseBody.use { it.write(body.toByteArray()) }
        }
        hub.start()
        val dir = Files.createTempDirectory("link")
        val pb = ProcessBuilder("python3", backend().path)
            .directory(backend().parentFile)
        pb.environment()["ALVAOS_LINK_DIR"] = dir.toString()
        pb.environment()["ALVAOS_LINK_TEST"] = """{"services": {"hub": ${hub.address.port}}}"""
        embedPorts = File(dir.toFile(), "embed_ports.json").path
        File(embedPorts).writeText("{\"ports\": []}")
        pb.environment()["ALVAOS_EMBED_PORTS"] = embedPorts
        pb.environment()["PYTHONUNBUFFERED"] = "1"
        pb.redirectError(File(dir.toFile(), "daemon.err"))
        daemonLog = File(dir.toFile(), "daemon.err")
        daemon = pb.start()
        val reader = daemon!!.inputStream.bufferedReader()
        val line = readFirstJson(reader)
        // Parsed as JSON: an IPv6 address like "[::1]:1234" has a "]" in it, which a regex for the list would cut.
        val found = Json.parseToJsonElement(line).jsonObject
        nasId = found["node_id"]!!.jsonPrimitive.content
        hints = found["addresses"]!!.jsonArray.map { it.jsonPrimitive.content }
    }

    private fun readFirstJson(reader: BufferedReader): String {
        val deadline = System.currentTimeMillis() + 30_000
        while (System.currentTimeMillis() < deadline) {
            val line = reader.readLine() ?: break
            if (line.startsWith("{")) return line
        }
        error("The daemon did not start")
    }

    @AfterTest
    fun stop() {
        daemon?.destroyForcibly()
        if (::hub.isInitialized) hub.stop(0)
    }

    @Test
    fun aPhonePairsAndThenReachesTheHubThroughTheLocalProxy() = runBlocking {
        val phone = LinkClient(directOnly = true)
        phone.start()
        val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
        try {
            // Not paired yet: the Hub is not open to a stranger, only pairing is.
            val proxy = LinkProxy(phone, nasId, hints)
            proxy.start(scope)
            val http = OkHttpClient.Builder().callTimeout(10, TimeUnit.SECONDS).build()
            assertFailsWith<Exception> { http.newCall(Request.Builder().url(proxy.baseUrl + "/api/me").build()).execute().use { it.body!!.string() } }
            // A wrong code pairs nothing.
            val failure = runCatching { phone.pairDevice(nasId, "WRONG000", DeviceInfo("Pixel 8"), hints) }.exceptionOrNull()
            val e = failure as? HubException ?: throw AssertionError(
                "a wrong code must be refused by the NAS, but: ${failure?.let { it::class.simpleName + ": " + it.message }}\n" +
                    "--- daemon ---\n${daemonLog?.takeIf { it.exists() }?.readText()}", failure)
            assertTrue(e.message!!.contains("not valid"))
            // The right one trades for the session of this phone.
            val paired = phone.pairDevice(nasId, "ABCD1234", DeviceInfo("Pixel 8", "Pixel 8"), hints)
            assertEquals("tok", paired.token)
            assertEquals("anna", paired.user)
            assertTrue(seen.any { it.startsWith("pair 127.95.0.1 ") })              // the NAS pairs from its own pairing address
            // Now the Hub is open to this phone: through the proxy it is an ordinary web address.
            val answer = http.newCall(Request.Builder().url(proxy.baseUrl + "/api/me").build()).execute().use { it.body!!.string() }
            assertTrue(answer.contains("from the hub") && answer.contains("/api/me"), answer)
            assertTrue(Regex("\"you\":\"127\\.95\\.\\d+\\.\\d+\"").containsMatchIn(answer), answer)  // seen from this phone's own address
            assertTrue(phone.ping(nasId, hints) >= 0)
            proxy.stop()
        } finally {
            scope.cancel()
            phone.close()
        }
    }

    @Test
    fun anAppInTheHubsWindowGetsItsPortHereThroughLink() = runBlocking {
        val app = HttpServer.create(InetSocketAddress("127.0.0.1", 0), 0)
        app.createContext("/") { x ->
            val body = "app says hello to ${x.remoteAddress.address.hostAddress}"
            x.sendResponseHeaders(200, body.toByteArray().size.toLong())
            x.responseBody.use { it.write(body.toByteArray()) }
        }
        app.start()
        val phone = LinkClient(directOnly = true)
        phone.start()
        val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
        val http = OkHttpClient.Builder().callTimeout(10, TimeUnit.SECONDS).build()
        try {
            phone.pairDevice(nasId, "ABCD1234", DeviceInfo("Pixel 8", "Pixel 8"), hints)
            // The Hub lists the proxy's port for the daemon (here the app's own port stands in for it).
            val port = app.address.port
            File(embedPorts).writeText("{\"ports\": [$port]}")
            // Only ports from the first one up are listened on, and exactly these: the rest stop.
            val apps = AppPorts(phone, nasId, hints)
            val free = java.net.ServerSocket(0).use { it.localPort }
            apps.sync(scope, setOf(80, free))
            assertEquals(setOf(free), apps.ports())
            apps.sync(scope, emptySet())
            assertEquals(emptySet(), apps.ports())
            apps.stop()
            // A port on the list is carried to the app, from the phone's own address on the NAS side.
            val taken = java.net.ServerSocket(0).use { it.localPort }
            val local = LinkProxy(phone, nasId, hints, "app:$port")
            assertTrue(local.startExact(scope, taken))
            val got = http.newCall(Request.Builder().url("http://127.0.0.1:$taken/x").build()).execute().use { it.body!!.string() }
            assertTrue(got.startsWith("app says hello"), got)
            // A port the Hub did not list is refused by the NAS.
            val other = LinkProxy(phone, nasId, hints, "app:22")
            val otherPort = java.net.ServerSocket(0).use { it.localPort }
            assertTrue(other.startExact(scope, otherPort))
            assertFailsWith<Exception> { http.newCall(Request.Builder().url("http://127.0.0.1:$otherPort/x").build()).execute().use { it.body!!.string() } }
            local.stop(); other.stop()
        } finally {
            scope.cancel()
            phone.close()
            app.stop(0)
        }
    }

    @Test
    fun thePhoneKeepsItsKeyBetweenStarts() = runBlocking {
        val first = LinkClient(directOnly = true)
        val again = LinkClient(first.secretBytes(), directOnly = true)
        assertEquals(first.id(), again.id())
        assertEquals(64, first.id().length)
    }

    @Test
    fun aLinkAddressIsSixtyFourHexDigits() {
        assertEquals(32, ("ab".repeat(32)).hexBytes().size)
        assertFailsWith<IllegalArgumentException> { "xyz".hexBytes() }
        assertEquals("ab".repeat(32), ("ab".repeat(32)).hexBytes().toHex())
    }
}
