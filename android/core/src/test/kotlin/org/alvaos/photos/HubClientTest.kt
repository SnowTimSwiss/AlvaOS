package org.alvaos.photos

import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import java.io.ByteArrayInputStream
import kotlin.test.AfterTest
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFailsWith
import kotlin.test.assertTrue

class HubClientTest {
    private val server = MockWebServer().apply { start() }
    private val client = HubClient(server.url("/").toString())

    @AfterTest
    fun stop() = server.shutdown()

    private fun json(body: String, code: Int = 200) =
        MockResponse().setResponseCode(code).setHeader("Content-Type", "application/json").setBody(body)

    @Test
    fun signsInAndSendsTheSessionWithEveryCall() {
        server.enqueue(json("""{"success": true, "user": "anna"}""")
            .addHeader("Set-Cookie", "alvaos_files=tok123; HttpOnly; Path=/; SameSite=Strict"))
        server.enqueue(json("""{"success": true, "phone": {"id": "0123456789ab", "name": "Pixel", "folder": "Pixel"}}"""))
        assertEquals("tok123", client.signIn("anna", "pw"))
        assertEquals("0123456789ab", client.addPhone("Pixel").id)
        server.takeRequest()
        val add = server.takeRequest()
        assertEquals("alvaos_files=tok123", add.getHeader("Cookie"))
        assertEquals("1", add.getHeader("X-AlvaOS-Files"))
        assertEquals("""{"name":"Pixel"}""", add.body.readUtf8())
    }

    @Test
    fun theNasSentenceComesThrough() {
        server.enqueue(json("""{"error": "That name or password is not right."}""", 401))
        val e = assertFailsWith<HubException> { client.signIn("anna", "bad") }
        assertEquals("That name or password is not right.", e.message)
        assertTrue(e.signedOut)
    }

    @Test
    fun aPlanIsReadWithItsUploadsAndDeletes() {
        server.enqueue(json("""{"success": true, "upload": [{"id": "i1", "share": "anna-home",
            "path": "Photos/Pixel/Camera", "name": "a.jpg"}], "delete_on_phone": ["i9"], "trashed": 2, "forgotten": 0}"""))
        val plan = client.plan("0123456789ab", listOf(PlanItem("i1", "Camera", "a.jpg", 3, 1000)), listOf("i5"), emptyList())
        assertEquals("a.jpg", plan.upload.single().name)
        assertEquals(listOf("i9"), plan.delete_on_phone)
        val sent = server.takeRequest().body.readUtf8()
        assertTrue(sent.contains(""""deleted":["i5"]""") && sent.contains(""""album":"Camera""""))
    }

    @Test
    fun anUploadGoesOnWhereItStoppedAfterADroppedConnection() {
        val target = Upload("i1", "anna-home", "Photos/Pixel 8/Camera", "a b.jpg")
        val data = ByteArray(40) { it.toByte() }
        server.enqueue(json("{}"))                                   // abort an old attempt
        server.enqueue(MockResponse().setResponseCode(503))          // the piece fails
        server.enqueue(json("""{"size": 0}"""))                      // status: nothing there
        server.enqueue(json("""{"size": 40}"""))                     // the piece again
        server.enqueue(json("{}"))                                   // finish
        client.upload(target, data.size.toLong(), 1791380000000) { offset -> ByteArrayInputStream(data, offset.toInt(), 40) }
        val abort = server.takeRequest()
        assertEquals("/api/upload/abort", abort.path)
        server.takeRequest()
        assertTrue(server.takeRequest().path!!.startsWith("/api/upload/status?share=anna-home&path=Photos%2FPixel%208%2FCamera&name=a%20b.jpg"))
        val piece = server.takeRequest()
        assertTrue(piece.path!!.endsWith("&offset=0"))
        assertEquals(40, piece.body.size.toInt())
        val finish = server.takeRequest().body.readUtf8()
        assertTrue(finish.contains(""""modified":1791380000000""") && finish.contains(""""size":40"""))
    }
}
