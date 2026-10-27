package ru.uzel.bridge.core

import com.sun.net.httpserver.HttpServer
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test
import java.net.InetSocketAddress

class CoreApiTest {

    // Ответ /ui/api/day в точности такой, как отдаёт ядро 23.10.2026.
    private val dayJson = """
        {"date":"2026-10-23","conversations":[{"id":60,"started_at":1792737199.313,
        "ended_at":1792737797.76,"started":"13:33","ended":"13:43","duration":"9 мин",
        "status":"noise","summary":null,"tags":["вид:дело"],"participants":[],
        "utterances":43,"facts":1,"tasks":2}],
        "facts":[{"id":5,"text":"Договорились: КП до пятницы","said":"13:35","conversation_id":60}],
        "tasks":[{"id":9,"text":"Запросить КП","assignee":null,"due":"","status":"open",
        "exported":false,"conversation_id":60}],
        "totals":{"conversations":1,"facts":1,"tasks":1,"speech":"21 мин"}}
    """.trimIndent()

    @Test
    fun `лента дня разбирается, null не превращается в строку`() {
        val day = parseDay(dayJson)
        assertEquals("2026-10-23", day.date)
        assertEquals("21 мин", day.speech)
        val c = day.conversations.single()
        assertEquals(60L, c.id)
        assertNull(c.summary)
        assertEquals(listOf("вид:дело"), c.tags)
        assertEquals(2, c.tasks)
        val t = day.tasks.single()
        assertNull(t.assignee)
        assertNull("пустой срок — не срок", t.due)
        assertEquals(60L, t.conversationId)
        assertEquals("13:35", day.facts.single().said)
        assertNull(day.error)
    }

    @Test
    fun `приватность — русские ключи ответа ядра`() {
        val on = parsePrivacy("""{"запись":"идёт","причина":"разрешено","код":"allowed","до":null}""")
        assertTrue(on.recording)
        assertNull(on.until)
        val off = parsePrivacy("""{"запись":"выключена","причина":"вручную","код":"muted","до":"2026-10-23T18:10:00"}""")
        assertFalse(off.recording)
        assertEquals("2026-10-23T18:10:00", off.until)
    }

    @Test
    fun `разговор с расшифровкой и поиск`() {
        val d = parseConversation(
            """{"id":7,"started_at":1,"ended_at":2,"started":"10:00","ended":"10:05","duration":"5 мин",
            "status":"closed","summary":"Про кирпич","tags":[],"participants":["Пётр"],"utterances":2,
            "facts":[],"tasks":[],"transcript":[{"id":1,"started":"10:00:01","speaker":null,
            "text":"привет","status":"done","reject_reason":null,"is_private":true}]}""",
        )
        assertEquals("Про кирпич", d.card.summary)
        assertEquals(listOf("Пётр"), d.card.participants)
        assertTrue(d.transcript.single().isPrivate)
        assertNull(d.transcript.single().speaker)

        val s = parseSearch(
            """{"origin":"смысловым поиском","query":"кирпич","results":[{"utterance_id":3,
            "conversation_id":7,"started":"23.10 10:00","speaker":null,"text":"про облицовку",
            "score":0.71,"is_private":false},{"utterance_id":4,"conversation_id":null,
            "started":"23.10 10:01","speaker":"Я","text":"ещё","score":null,"is_private":false}]}""",
        )
        assertEquals(2, s.hits.size)
        assertEquals(0.71, s.hits[0].score!!, 1e-9)
        assertNull(s.hits[1].score)
        assertNull(s.hits[1].conversationId)
    }

    // ---- против настоящего HTTP-сервера из JDK ----

    private lateinit var server: HttpServer
    private var lastAuth: String? = null
    private var lastMethod: String? = null

    @Before
    fun up() {
        server = HttpServer.create(InetSocketAddress("127.0.0.1", 0), 0)
        server.createContext("/") { ex ->
            lastAuth = ex.requestHeaders.getFirst("Authorization")
            lastMethod = ex.requestMethod
            val path = ex.requestURI.path
            val (code, body) = when {
                lastAuth != "Bearer s3cret" -> 401 to """{"detail":"нет"}"""
                path == "/ui/api/day" -> 200 to dayJson
                path == "/privacy/mute" -> 200 to """{"запись":"выключена","причина":"x","код":"muted","до":"2026-10-23T18:00:00"}"""
                else -> 404 to "{}"
            }
            val bytes = body.toByteArray(Charsets.UTF_8)
            ex.sendResponseHeaders(code, bytes.size.toLong())
            ex.responseBody.use { it.write(bytes) }
        }
        server.start()
    }

    @After
    fun down() = server.stop(0)

    private fun url() = "http://127.0.0.1:${server.address.port}/"

    @Test
    fun `токен уходит заголовком, ответ разбирается`() {
        val r = CoreApi(url(), "s3cret").day()
        assertTrue(r is ApiResult.Ok)
        assertEquals("Bearer s3cret", lastAuth)
        assertEquals(1, (r as ApiResult.Ok).value.conversations.size)
    }

    @Test
    fun `без токена — понятная причина, а не исключение`() {
        val r = CoreApi(url(), "").day()
        assertTrue(r is ApiResult.Fail)
        assertEquals(401, (r as ApiResult.Fail).code)
        assertTrue(r.message.contains("токен"))
    }

    @Test
    fun `заглушка — POST`() {
        val r = CoreApi(url(), "s3cret").mute(10)
        assertEquals("POST", lastMethod)
        assertFalse((r as ApiResult.Ok).value.recording)
    }

    @Test
    fun `ядро выключено — Fail без исключения`() {
        val dead = "http://127.0.0.1:1/"
        val r = CoreApi(dead, null, timeoutMs = 500).status()
        assertTrue(r is ApiResult.Fail)
    }

    @Test
    fun `битый JSON — Fail, а не падение экрана`() {
        val r: ApiResult<String> = ApiResult.Ok("не json")
        assertTrue(r.map(::parseDay) is ApiResult.Fail)
    }
}
