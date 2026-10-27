package ru.uzel.bridge.core

import com.sun.net.httpserver.HttpExchange
import com.sun.net.httpserver.HttpServer
import org.junit.After
import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Before
import org.junit.Test
import java.io.File
import java.net.InetSocketAddress
import java.nio.file.Files
import java.util.concurrent.ConcurrentLinkedQueue

/** Тесты связки клиент + спул (ТЗ P8, пункт 6). */
class UploaderTest {

    private lateinit var server: HttpServer
    private lateinit var spool: DiskSpool
    private lateinit var uploader: Uploader
    private lateinit var dir: File

    /** Очередь кодов ответов; пустая — отвечаем 202. */
    private val responses = ConcurrentLinkedQueue<Int>()
    private val bodies = ConcurrentLinkedQueue<ByteArray>()

    @Before
    fun setUp() {
        server = HttpServer.create(InetSocketAddress("127.0.0.1", 0), 0)
        server.createContext("/") { exchange: HttpExchange ->
            bodies.add(exchange.requestBody.readBytes())
            val code = responses.poll() ?: 202
            exchange.sendResponseHeaders(code, -1)
            exchange.close()
        }
        server.start()

        dir = Files.createTempDirectory("uzel-uploader-test").toFile()
        spool = DiskSpool(dir)
        uploader = Uploader(
            client = CoreClient(baseUrl = "http://127.0.0.1:${server.address.port}", uid = "phone-1"),
            spool = spool,
        )
    }

    @After
    fun tearDown() {
        server.stop(0)
        dir.deleteRecursively()
    }

    @Test
    fun failedSubmitGoesToSpool() {
        responses.add(500)
        assertEquals(SendResult.FAILED, uploader.submit(byteArrayOf(1), deviceTimeMs = 10L))
        assertEquals(1, spool.count())
        assertEquals(1, uploader.spooled)
        assertEquals(0, uploader.sent)
    }

    @Test
    fun mutedSubmitIsDiscarded() {
        responses.add(204)
        assertEquals(SendResult.MUTED, uploader.submit(byteArrayOf(1)))
        assertEquals(0, spool.count())
        assertEquals(1, uploader.muted)
        assertEquals(0, uploader.spooled)
    }

    @Test
    fun rejectedSubmitIsDiscarded() {
        responses.add(400)
        assertEquals(SendResult.REJECTED, uploader.submit(byteArrayOf(1)))
        assertEquals(0, spool.count())
        assertEquals(1, uploader.dropped)
        assertEquals(0, uploader.spooled)
    }

    @Test
    fun flushAfterRecoverySendsAndClearsSpool() {
        responses.add(500)
        responses.add(500)
        val first = byteArrayOf(1)
        val second = byteArrayOf(2)
        uploader.submit(first, deviceTimeMs = 100L)
        uploader.submit(second, deviceTimeMs = 200L)
        assertEquals(2, spool.count())

        // Ядро ожило — по умолчанию сервер отвечает 202.
        val delivered = uploader.flush()
        assertEquals(2, delivered)
        assertEquals(0, spool.count())
        assertEquals(2, uploader.sent)
        // Порядок сохранён: сначала старый фрагмент, потом новый.
        assertArrayEquals(first, bodies.poll())
        assertArrayEquals(second, bodies.poll())
    }

    @Test
    fun flushStopsOnRepeatedFailureAndKeepsOrder() {
        responses.add(500)
        responses.add(500)
        responses.add(500)
        val first = byteArrayOf(1)
        val second = byteArrayOf(2)
        val third = byteArrayOf(3)
        uploader.submit(first)
        uploader.submit(second)
        uploader.submit(third)

        // Первый уходит (202 из очереди), второй натыкается на 500 — досылка прекращается.
        responses.add(202)
        responses.add(500)
        val delivered = uploader.flush()
        assertEquals(1, delivered)
        assertEquals(2, spool.count())
        assertArrayEquals(first, bodies.poll())
        // В спуле остались второй и третий, в порядке накопления.
        val pending = spool.pending(10)
        assertArrayEquals(second, pending[0].read())
        assertArrayEquals(third, pending[1].read())
    }
}
