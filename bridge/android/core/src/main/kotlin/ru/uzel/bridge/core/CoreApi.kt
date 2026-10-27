package ru.uzel.bridge.core

import org.json.JSONArray
import org.json.JSONObject
import java.net.HttpURLConnection
import java.net.URL
import java.net.URLEncoder

/** Разговор в ленте дня — без дословных реплик (как `/ui/api/day`). */
data class ConversationCard(
    val id: Long,
    val startedAt: Double,
    val endedAt: Double,
    val started: String,
    val ended: String,
    val duration: String,
    val status: String,
    val summary: String?,
    val tags: List<String>,
    val participants: List<String>,
    val utterances: Int,
    val facts: Int,
    val tasks: Int,
)

data class TaskItem(
    val id: Long,
    val text: String,
    val assignee: String?,
    val due: String?,
    val status: String?,
    val conversationId: Long?,
)

data class FactItem(
    val id: Long,
    val text: String,
    val said: String?,
    val conversationId: Long?,
)

data class DayFeed(
    val date: String,
    val conversations: List<ConversationCard>,
    val tasks: List<TaskItem>,
    val facts: List<FactItem>,
    val speech: String,
    val error: String?,
)

data class Utterance(
    val id: Long,
    val started: String,
    val speaker: String?,
    val text: String,
    val isPrivate: Boolean,
)

data class ConversationDetail(
    val card: ConversationCard,
    val tasks: List<TaskItem>,
    val facts: List<FactItem>,
    val transcript: List<Utterance>,
)

data class SearchHit(
    val utteranceId: Long,
    val conversationId: Long?,
    val started: String,
    val speaker: String?,
    val text: String,
    val score: Double?,
    val isPrivate: Boolean,
)

data class SearchResult(val origin: String, val hits: List<SearchHit>, val error: String?)

/** Состояние приватности ядра (`/privacy`): ключи в ответе — русские. */
data class PrivacyState(
    val recording: Boolean,
    val reason: String?,
    val code: String?,
    val until: String?,
)

/** Что видит ядро в шапке окна (`/ui/api/status`) — нужное телефону. */
data class CoreStatus(
    val version: String?,
    val pending: Int,
    val conversations: Int,
    val tasks: Int,
    val facts: Int,
    val privacy: PrivacyState?,
)

/** Ответ ядра: либо значение, либо человеческая причина отказа. */
sealed class ApiResult<out T> {
    data class Ok<T>(val value: T) : ApiResult<T>()
    data class Fail(val message: String, val code: Int? = null) : ApiResult<Nothing>()
}

/**
 * Чтение памяти ядра для экрана телефона: лента дня, разговор, поиск,
 * приватность. Тот же `HttpURLConnection`, что у [CoreClient], — без
 * сторонних HTTP-библиотек. Разбор JSON вынесен в функции `parse*`, чтобы
 * их можно было проверить без сети.
 *
 * Токен идёт заголовком `Authorization: Bearer …` — не светится в журналах
 * доступа так, как параметр запроса.
 */
class CoreApi(
    baseUrl: String,
    private val token: String? = null,
    private val timeoutMs: Int = 8_000,
) {
    private val base: String = baseUrl.trimEnd('/')

    fun status(): ApiResult<CoreStatus> = get("/ui/api/status").map(::parseStatus)

    fun day(date: String? = null): ApiResult<DayFeed> =
        get("/ui/api/day" + (date?.let { "?date=" + enc(it) } ?: "")).map(::parseDay)

    fun conversation(id: Long, withUtterances: Boolean): ApiResult<ConversationDetail> =
        get("/ui/api/conversation/$id?with_utterances=$withUtterances").map(::parseConversation)

    fun search(query: String, limit: Int = 30): ApiResult<SearchResult> =
        get("/ui/api/search?q=" + enc(query) + "&limit=$limit").map(::parseSearch)

    fun privacy(): ApiResult<PrivacyState> = get("/privacy").map(::parsePrivacy)

    /** Заглушить запись на [minutes] минут. */
    fun mute(minutes: Int): ApiResult<PrivacyState> =
        post("/privacy/mute?minutes=$minutes").map(::parsePrivacy)

    /** Бессрочный «приват» — до явного снятия. */
    fun pause(): ApiResult<PrivacyState> = post("/privacy/pause").map(::parsePrivacy)

    fun unmute(): ApiResult<PrivacyState> = post("/privacy/unmute").map(::parsePrivacy)

    private fun get(path: String): ApiResult<String> = request("GET", path)

    private fun post(path: String): ApiResult<String> = request("POST", path)

    private fun request(method: String, path: String): ApiResult<String> {
        return try {
            val conn = URL(base + path).openConnection() as HttpURLConnection
            conn.connectTimeout = timeoutMs
            conn.readTimeout = timeoutMs
            conn.requestMethod = method
            if (!token.isNullOrBlank()) conn.setRequestProperty("Authorization", "Bearer $token")
            if (method == "POST") {
                conn.doOutput = true
                conn.setFixedLengthStreamingMode(0)
                conn.outputStream.close()
            }
            val code = conn.responseCode
            val stream = if (code in 200..299) conn.inputStream else conn.errorStream
            val body = stream?.use { it.readBytes().toString(Charsets.UTF_8) } ?: ""
            conn.disconnect()
            when {
                code in 200..299 -> ApiResult.Ok(body)
                code == 401 -> ApiResult.Fail("ядро не пустило: проверьте токен", code)
                code == 404 -> ApiResult.Fail("не найдено", code)
                else -> ApiResult.Fail("ядро ответило $code", code)
            }
        } catch (e: Exception) {
            ApiResult.Fail("нет связи с ядром")
        }
    }

    private fun enc(s: String): String = URLEncoder.encode(s, "UTF-8")
}

/** Ошибка разбора — тоже `Fail`, а не исключение в экран. */
inline fun <T, R> ApiResult<T>.map(transform: (T) -> R): ApiResult<R> = when (this) {
    is ApiResult.Ok -> try {
        ApiResult.Ok(transform(value))
    } catch (e: Exception) {
        ApiResult.Fail("непонятный ответ ядра")
    }
    is ApiResult.Fail -> this
}

// ---- разбор JSON ----
// На Android `optString` у явного null отдаёт строку "null" — поэтому всюду
// сначала `isNull`.

internal fun JSONObject.str(key: String): String? =
    if (!has(key) || isNull(key)) null else optString(key)

private fun JSONObject.long(key: String): Long? =
    if (!has(key) || isNull(key)) null else optLong(key)

private fun JSONArray?.objects(): List<JSONObject> =
    if (this == null) emptyList() else (0 until length()).mapNotNull { optJSONObject(it) }

private fun JSONArray?.strings(): List<String> =
    if (this == null) emptyList() else (0 until length()).mapNotNull { i ->
        if (isNull(i)) null else optString(i)
    }

fun parseCard(o: JSONObject) = ConversationCard(
    id = o.getLong("id"),
    startedAt = o.optDouble("started_at", 0.0),
    endedAt = o.optDouble("ended_at", 0.0),
    started = o.str("started") ?: "",
    ended = o.str("ended") ?: "",
    duration = o.str("duration") ?: "",
    status = o.str("status") ?: "",
    summary = o.str("summary")?.takeIf { it.isNotBlank() },
    tags = o.optJSONArray("tags").strings(),
    participants = o.optJSONArray("participants").strings(),
    utterances = o.optInt("utterances"),
    facts = o.optInt("facts"),
    tasks = o.optInt("tasks"),
)

fun parseTask(o: JSONObject) = TaskItem(
    id = o.getLong("id"),
    text = o.str("text") ?: "",
    assignee = o.str("assignee")?.takeIf { it.isNotBlank() },
    due = o.str("due")?.takeIf { it.isNotBlank() },
    status = o.str("status"),
    conversationId = o.long("conversation_id"),
)

fun parseFact(o: JSONObject) = FactItem(
    id = o.getLong("id"),
    text = o.str("text") ?: "",
    said = o.str("said"),
    conversationId = o.long("conversation_id"),
)

fun parseDay(body: String): DayFeed {
    val o = JSONObject(body)
    return DayFeed(
        date = o.str("date") ?: "",
        conversations = o.optJSONArray("conversations").objects().map(::parseCard),
        tasks = o.optJSONArray("tasks").objects().map(::parseTask),
        facts = o.optJSONArray("facts").objects().map(::parseFact),
        speech = o.optJSONObject("totals")?.str("speech") ?: "0 с",
        error = o.str("error"),
    )
}

fun parseConversation(body: String): ConversationDetail {
    val o = JSONObject(body)
    return ConversationDetail(
        card = parseCard(o),
        tasks = o.optJSONArray("tasks").objects().map(::parseTask),
        facts = o.optJSONArray("facts").objects().map(::parseFact),
        transcript = o.optJSONArray("transcript").objects().map {
            Utterance(
                id = it.getLong("id"),
                started = it.str("started") ?: "",
                speaker = it.str("speaker"),
                text = it.str("text") ?: "",
                isPrivate = it.optBoolean("is_private"),
            )
        },
    )
}

fun parseSearch(body: String): SearchResult {
    val o = JSONObject(body)
    return SearchResult(
        origin = o.str("origin") ?: "",
        hits = o.optJSONArray("results").objects().map {
            SearchHit(
                utteranceId = it.getLong("utterance_id"),
                conversationId = it.long("conversation_id"),
                started = it.str("started") ?: "",
                speaker = it.str("speaker"),
                text = it.str("text") ?: "",
                score = if (it.isNull("score")) null else it.optDouble("score"),
                isPrivate = it.optBoolean("is_private"),
            )
        },
        error = o.str("error"),
    )
}

fun parsePrivacy(body: String): PrivacyState = parsePrivacyObject(JSONObject(body))

private fun parsePrivacyObject(o: JSONObject) = PrivacyState(
    recording = o.str("запись") != "выключена",
    reason = o.str("причина"),
    code = o.str("код"),
    until = o.str("до"),
)

fun parseStatus(body: String): CoreStatus {
    val o = JSONObject(body)
    val db = o.optJSONObject("db")
    return CoreStatus(
        version = o.str("version"),
        pending = o.optJSONObject("queue")?.optInt("pending") ?: 0,
        conversations = db?.optInt("conversations") ?: 0,
        tasks = db?.optInt("tasks") ?: 0,
        facts = db?.optInt("facts") ?: 0,
        privacy = o.optJSONObject("privacy")?.let(::parsePrivacyObject),
    )
}
