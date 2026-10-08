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
    private lateinit var nasId: String
    private lateinit var hints: List<String>
    private val seen = mutableListOf<String>()

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
        pb.environment()["PYTHONUNBUFFERED"] = "1"
        daemon = pb.start()
        val reader = daemon!!.inputStream.bufferedReader()
        val line = readFirstJson(reader)
        nasId = Regex("\"node_id\": \"([0-9a-f]{64})\"").find(line)!!.groupValues[1]
        hints = Regex("\"addresses\": \\[([^]]*)]").find(line)!!.groupValues[1].split(",")
            .map { it.trim().trim('"') }.filter { it.isNotEmpty() }
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
            val e = assertFailsWith<HubException> { phone.pairDevice(nasId, "WRONG000", DeviceInfo("Pixel 8"), hints) }
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
