package ru.uzel.bridge.core

import com.sun.net.httpserver.HttpExchange
import com.sun.net.httpserver.HttpServer
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test
import java.net.InetSocketAddress
import java.util.concurrent.atomic.AtomicInteger

/** Один записанный запрос: путь, query и тело целиком. */
private class Recorded(path: String, query: String?, body: ByteArray, contentType: String?) {
    val path = path
    val query = query ?: ""
    val body = body
    val contentType = contentType ?: ""
}

/** Тесты клиента ядра против настоящего HTTP-сервера из JDK (ТЗ P8, пункт 5). */
class CoreClientTest {

    private lateinit var server: HttpServer
    private lateinit var client: CoreClient
    private val recorded = java.util.concurrent.CopyOnWriteArrayList<Recorded>()
    private val status = AtomicInteger(200)

    @Before
    fun setUp() {
        server = HttpServer.create(InetSocketAddress("127.0.0.1", 0), 0)
        server.createContext("/") { exchange: HttpExchange ->
            val body = exchange.requestBody.readBytes()
            recorded.add(
                Recorded(
                    path = exchange.requestURI.path,
                    query = exchange.requestURI.rawQuery,
                    body = body,
                    contentType = exchange.requestHeaders.getFirst("Content-Type"),
                )
            )
            exchange.sendResponseHeaders(status.get(), -1)
            exchange.close()
        }
        server.start()
        client = CoreClient(
            baseUrl = "http://127.0.0.1:${server.address.port}",
            uid = "phone-1",
        )
    }

    @After
    fun tearDown() {
        server.stop(0)
    }

    @Test
    fun acceptedOn202() {
        status.set(202)
        val payload = byteArrayOf(1, 2, 3)
        assertEquals(SendResult.ACCEPTED, client.sendAudio(payload, deviceTimeMs = 7_000L))

        val request = recorded.single()
        // Сервер увидел тело целиком и правильный путь.
        assertEquals("/ingest/opus", request.path)
        org.junit.Assert.assertArrayEquals(payload, request.body)
        // Параметры запроса на месте.
        assertTrue(request.query.contains("uid=phone-1"))
        assertTrue(request.query.contains("source=android"))
        assertTrue(request.query.contains("device_time_ms=7000"))
        assertEquals("application/octet-stream", request.contentType)
    }

    @Test
    fun deviceTimeOmittedWhenNull() {
        status.set(202)
        client.sendAudio(byteArrayOf(1))
        assertFalse(recorded.single().query.contains("device_time_ms"))
    }

    @Test
    fun tokenSentAsQueryParameterWhenSet() {
        status.set(202)
        val secured = CoreClient(
            baseUrl = "http://127.0.0.1:${server.address.port}",
            uid = "phone-1",
            token = "секрет!",
        )
        assertEquals(SendResult.ACCEPTED, secured.sendAudio(byteArrayOf(1)))

        val request = recorded.single()
        assertTrue(request.query.contains("token=" + java.net.URLEncoder.encode("секрет!", "UTF-8")))
    }

    @Test
    fun tokenOmittedWhenBlank() {
        status.set(202)
        client.sendAudio(byteArrayOf(1))
        assertFalse(recorded.single().query.contains("token"))
    }

    @Test
    fun mutedOn204() {
        status.set(204)
        assertEquals(SendResult.MUTED, client.sendAudio(byteArrayOf(1)))
    }

    @Test
    fun rejectedOn400() {
        status.set(400)
        assertEquals(SendResult.REJECTED, client.sendAudio(byteArrayOf(1)))
    }

    @Test
    fun failedOn500() {
        status.set(500)
        assertEquals(SendResult.FAILED, client.sendAudio(byteArrayOf(1)))
    }

    @Test
    fun failedWhenServerUnreachable() {
        // Порт освобождён остановкой сервера-донора: соединение отвергается.
        val donor = HttpServer.create(InetSocketAddress("127.0.0.1", 0), 0)
        val port = donor.address.port
        donor.stop(0)
        val dead = CoreClient(baseUrl = "http://127.0.0.1:$port", uid = "phone-1")
        assertEquals(SendResult.FAILED, dead.sendAudio(byteArrayOf(1)))
    }

    @Test
    fun reportStatusSendsValidJson() {
        status.set(200)
        val ok = client.reportStatus(
            batteryPercent = 77,
            codec = "opus-16k",
            connected = true,
            packets = 10,
            lost = 2,
            spooled = 3,
        )
        assertTrue(ok)

        val request = recorded.single()
        assertEquals("/device/status", request.path)
        assertEquals("application/json; charset=utf-8", request.contentType)
        val json = String(request.body, Charsets.UTF_8)
        // Валидный JSON с ожидаемыми полями (без библиотек — по содержимому).
        assertTrue(json.contains("\"uid\":\"phone-1\""))
        assertTrue(json.contains("\"battery_percent\":77"))
        assertTrue(json.contains("\"codec\":\"opus-16k\""))
        assertTrue(json.contains("\"connected\":true"))
        assertTrue(json.contains("\"packets\":10"))
        assertTrue(json.contains("\"lost\":2"))
        assertTrue(json.contains("\"spooled\":3"))
    }

    @Test
    fun healthReflectsAvailability() {
        status.set(200)
        assertTrue(client.health())
        status.set(500)
        assertFalse(client.health())
    }
}
