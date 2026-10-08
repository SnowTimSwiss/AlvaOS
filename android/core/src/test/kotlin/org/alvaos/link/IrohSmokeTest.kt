package org.alvaos.link

import computer.iroh.Endpoint
import computer.iroh.EndpointOptions
import computer.iroh.SecretKey
import computer.iroh.presetN0DisableRelay
import kotlinx.coroutines.runBlocking
import kotlin.test.Test
import kotlin.test.assertEquals

class IrohSmokeTest {
    @Test
    fun anEndpointCanBeBoundOnTheLoopback() = runBlocking {
        val key = SecretKey.generate()
        val endpoint = Endpoint.bind(EndpointOptions(preset = presetN0DisableRelay(), bindAddr = "127.0.0.1:0",
            secretKey = key.toBytes(), alpns = listOf("x".toByteArray())))
        assertEquals(key.public().toBytes().toList(), endpoint.id().toBytes().toList())
        endpoint.close()
    }
}
