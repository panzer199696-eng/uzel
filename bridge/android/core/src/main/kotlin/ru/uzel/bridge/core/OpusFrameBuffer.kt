package ru.uzel.bridge.core

import java.io.ByteArrayOutputStream

/**
 * Максимальная длина кадра: uint16 LE — больше в префикс длины не влезает
 * (как MAX_FRAME_BYTES в `core/uzel/opus_stream.py`).
 */
const val MAX_FRAME_BYTES = 65535

/**
 * Копит кадры Opus между отправками и отдаёт тело запроса для
 * `POST /ingest/opus` в формате `opus-frames/1`.
 *
 * Потокобезопасен: кадры приходят с BLE-потока, сливается буфер из сетевого.
 */
class OpusFrameBuffer {
    private val frames = ArrayList<ByteArray>()
    private var oldestAddMs: Long = -1
    private val lock = Any()

    /** Сколько кадров отброшено из-за превышения длины. */
    var droppedOversized: Int = 0
        private set

    /** Число кадров в буфере (для статуса в уведомлении). */
    val frameCount: Int
        get() = synchronized(lock) { frames.size }

    /** Добавляет кадр. Пустые кадры игнорируются, длиннее 65535 — отбрасываются. */
    fun add(frame: ByteArray) {
        synchronized(lock) {
            if (frame.isEmpty()) return
            if (frame.size > MAX_FRAME_BYTES) {
                droppedOversized++
                return
            }
            if (frames.isEmpty()) oldestAddMs = System.currentTimeMillis()
            frames.add(frame)
        }
    }

    /**
     * Пора ли сливать буфер: накопилось `maxFrames` кадров или с момента
     * первого кадра в текущей партии прошло `flushMs`.
     */
    fun shouldFlush(nowMs: Long, flushMs: Long, maxFrames: Int): Boolean {
        synchronized(lock) {
            if (frames.isEmpty()) return false
            if (frames.size >= maxFrames) return true
            return nowMs - oldestAddMs >= flushMs
        }
    }

    /**
     * Упаковывает накопленное в формат `opus-frames/1` и очищает буфер.
     * `null`, если сливать нечего.
     */
    fun drain(): ByteArray? {
        synchronized(lock) {
            if (frames.isEmpty()) return null
            val body = packFrames(frames)
            frames.clear()
            oldestAddMs = -1
            return body
        }
    }
}

/** Кадры Opus -> тело запроса: `uint16 LE длина` + кадр, подряд. */
fun packFrames(frames: List<ByteArray>): ByteArray {
    val out = ByteArrayOutputStream()
    for (frame in frames) {
        if (frame.isEmpty()) continue
        require(frame.size <= MAX_FRAME_BYTES) {
            "кадр Opus не помещается в формат: ${frame.size} байт при максимуме $MAX_FRAME_BYTES"
        }
        out.write(frame.size and 0xFF)
        out.write((frame.size shr 8) and 0xFF)
        out.write(frame)
    }
    return out.toByteArray()
}

/**
 * Симметричный разбор тела запроса обратно в кадры — им же тесты
 * проверяют упаковку. Битый формат — IllegalArgumentException.
 */
fun unpackFrames(payload: ByteArray): List<ByteArray> {
    val result = ArrayList<ByteArray>()
    var pos = 0
    while (pos < payload.size) {
        require(pos + 2 <= payload.size) { "обрезанный заголовок кадра: не хватает байтов длины" }
        val size = (payload[pos].toInt() and 0xFF) or ((payload[pos + 1].toInt() and 0xFF) shl 8)
        pos += 2
        require(size > 0) { "нулевая длина кадра: в формате допустимы только N > 0" }
        require(pos + size <= payload.size) {
            "обрезанный кадр Opus: заявлено $size байт, осталось ${payload.size - pos}"
        }
        result.add(payload.copyOfRange(pos, pos + size))
        pos += size
    }
    return result
}
