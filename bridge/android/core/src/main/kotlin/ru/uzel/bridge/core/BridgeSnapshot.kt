package ru.uzel.bridge.core

import org.json.JSONObject
import java.io.File

/**
 * Снимок состояния моста, который служба пишет на диск раз в пару секунд,
 * а экран читает. Через файл, а не через память: служба может жить в своём
 * процессе, и экран не должен от неё зависеть, чтобы показать цифры.
 */
data class BridgeSnapshot(
    val running: Boolean = false,
    val connected: Boolean = false,
    val batteryPercent: Int? = null,
    val codec: String? = null,
    val packets: Int = 0,
    val lost: Int = 0,
    val sent: Int = 0,
    val spooled: Int = 0,
    val muted: Int = 0,
    /** Когда пришёл последний кадр звука (мс), 0 — ещё не было. */
    val lastFrameAt: Long = 0,
    /** Когда служба поднялась (мс). */
    val startedAt: Long = 0,
    /** Последняя удачная отправка в ядро (мс), 0 — ещё не было. */
    val lastSentAt: Long = 0,
    /** Когда снимок записан (мс) — по нему видно, жива ли служба. */
    val writtenAt: Long = 0,
) {
    /** Доля потерянных пакетов, 0..1. */
    val lossRatio: Double
        get() = if (packets + lost == 0) 0.0 else lost.toDouble() / (packets + lost)

    /** Служба писала снимок недавно — значит, она жива. */
    fun isFresh(now: Long, maxAgeMs: Long = 15_000): Boolean =
        running && now - writtenAt <= maxAgeMs

    fun toJson(): String = JSONObject()
        .put("running", running)
        .put("connected", connected)
        .put("battery_percent", batteryPercent ?: JSONObject.NULL)
        .put("codec", codec ?: JSONObject.NULL)
        .put("packets", packets)
        .put("lost", lost)
        .put("sent", sent)
        .put("spooled", spooled)
        .put("muted", muted)
        .put("last_frame_at", lastFrameAt)
        .put("started_at", startedAt)
        .put("last_sent_at", lastSentAt)
        .put("written_at", writtenAt)
        .toString()

    fun writeTo(file: File) {
        file.parentFile?.mkdirs()
        val tmp = File(file.parentFile, file.name + ".tmp")
        tmp.writeText(toJson(), Charsets.UTF_8)
        if (!tmp.renameTo(file)) {
            file.delete()
            tmp.renameTo(file)
        }
    }

    companion object {
        fun parse(json: String): BridgeSnapshot {
            val o = JSONObject(json)
            return BridgeSnapshot(
                running = o.optBoolean("running"),
                connected = o.optBoolean("connected"),
                batteryPercent = if (o.isNull("battery_percent")) null else o.optInt("battery_percent"),
                codec = o.str("codec"),
                packets = o.optInt("packets"),
                lost = o.optInt("lost"),
                sent = o.optInt("sent"),
                spooled = o.optInt("spooled"),
                muted = o.optInt("muted"),
                lastFrameAt = o.optLong("last_frame_at"),
                startedAt = o.optLong("started_at"),
                lastSentAt = o.optLong("last_sent_at"),
                writtenAt = o.optLong("written_at"),
            )
        }

        /** Нет файла или он битый — «мост выключен», без исключений. */
        fun readFrom(file: File): BridgeSnapshot =
            try {
                if (file.exists()) parse(file.readText(Charsets.UTF_8)) else BridgeSnapshot()
            } catch (e: Exception) {
                BridgeSnapshot()
            }
    }
}
