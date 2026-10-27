package ru.uzel.bridge.core

import java.net.HttpURLConnection
import java.net.URL
import java.net.URLEncoder

/** Результат отправки фрагмента в ядро. */
enum class SendResult {
    /** 202 — ядро приняло фрагмент. */
    ACCEPTED,

    /** 204 — запись заглушена приватностью: фрагмент не нужен, выбросить. */
    MUTED,

    /** Прочие 4xx — формат отвергнут, повтор не поможет, выбросить. */
    REJECTED,

    /** 5xx или сеть недоступна — стоит повторить позже (через спул). */
    FAILED,
}

/**
 * Отправка в ядро на `java.net.HttpURLConnection` — без внешних
 * зависимостей (как у MCP-сервера в P6: меньше того, что сломается).
 *
 * Контракт — `core/uzel/app.py`:
 * - `POST /ingest/opus?uid=…&source=android&device_time_ms=…&token=…`,
 *   тело — кадры Opus с префиксом длины (`opus-frames/1`);
 * - `POST /device/status` — JSON о кулоне и канале;
 * - `GET /health` — проверка доступности.
 *
 * `token` (P11): ядро, слушающее не только localhost, без токена отвечает
 * 401 на всё, кроме `/health`. Пустой токен — локальное ядро без авторизации.
 */
class CoreClient(
    baseUrl: String,
    private val uid: String,
    private val source: String = "android",
    private val token: String? = null,
    private val connectTimeoutMs: Int = 10_000,
    private val readTimeoutMs: Int = 10_000,
) {
    private val base: String = baseUrl.trimEnd('/')

    /** Отправляет фрагмент (тело `opus-frames/1`). Исключений наружу не даёт. */
    fun sendAudio(payload: ByteArray, deviceTimeMs: Long? = null): SendResult {
        val url = buildString {
            append(base).append("/ingest/opus?uid=").append(urlEnc(uid))
            append("&source=").append(urlEnc(source))
            if (deviceTimeMs != null) append("&device_time_ms=").append(deviceTimeMs)
            if (!token.isNullOrBlank()) append("&token=").append(urlEnc(token))
        }
        val code = post(url, payload, "application/octet-stream")
        return when {
            code == null -> SendResult.FAILED
            code == 202 -> SendResult.ACCEPTED
            code == 204 -> SendResult.MUTED
            code in 400..499 -> SendResult.REJECTED
            else -> SendResult.FAILED
        }
    }

    /** Сообщает ядру состояние кулона и канала. `true` — отчёт дошёл. */
    fun reportStatus(
        batteryPercent: Int?,
        codec: String?,
        connected: Boolean,
        packets: Long,
        lost: Long,
        spooled: Int,
    ): Boolean {
        // JSON собирается строкой: значений с кириллицей нет, но экранирование честное.
        val json = buildString {
            append("{\"uid\":").append(jsonString(uid))
            append(",\"battery_percent\":").append(batteryPercent ?: "null")
            append(",\"codec\":").append(if (codec == null) "null" else jsonString(codec))
            append(",\"connected\":").append(connected)
            append(",\"packets\":").append(packets)
            append(",\"lost\":").append(lost)
            append(",\"spooled\":").append(spooled)
            append("}")
        }
        val url = buildString {
            append(base).append("/device/status")
            if (!token.isNullOrBlank()) append("?token=").append(urlEnc(token))
        }
        val code = post(
            url,
            json.toByteArray(Charsets.UTF_8),
            "application/json; charset=utf-8",
        )
        return code != null && code in 200..299
    }

    /** `true`, если ядро ответило на `GET /health`. */
    fun health(): Boolean {
        return try {
            val conn = URL("$base/health").openConnection() as HttpURLConnection
            conn.connectTimeout = connectTimeoutMs
            conn.readTimeout = readTimeoutMs
            conn.requestMethod = "GET"
            val ok = conn.responseCode in 200..299
            conn.disconnect()
            ok
        } catch (e: Exception) {
            false
        }
    }

    /** POST с телом; `null` — сеть недоступна или другой сбой транспорта. */
    private fun post(url: String, body: ByteArray, contentType: String): Int? {
        return try {
            val conn = URL(url).openConnection() as HttpURLConnection
            conn.connectTimeout = connectTimeoutMs
            conn.readTimeout = readTimeoutMs
            conn.doOutput = true
            conn.requestMethod = "POST"
            conn.setRequestProperty("Content-Type", contentType)
            conn.setFixedLengthStreamingMode(body.size)
            conn.outputStream.use { it.write(body) }
            val code = conn.responseCode
            conn.disconnect()
            code
        } catch (e: Exception) {
            null
        }
    }
}

private fun urlEnc(s: String): String = URLEncoder.encode(s, "UTF-8")

/** Минимальное экранирование строки в JSON-литерал. */
internal fun jsonString(s: String): String {
    val sb = StringBuilder("\"")
    for (c in s) {
        when {
            c == '"' -> sb.append("\\\"")
            c == '\\' -> sb.append("\\\\")
            c == '\n' -> sb.append("\\n")
            c == '\r' -> sb.append("\\r")
            c == '\t' -> sb.append("\\t")
            c < ' ' -> sb.append(String.format(java.util.Locale.ROOT, "\\u%04x", c.code))
            else -> sb.append(c)
        }
    }
    return sb.append('"').toString()
}
