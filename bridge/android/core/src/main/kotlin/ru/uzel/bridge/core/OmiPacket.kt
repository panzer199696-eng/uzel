package ru.uzel.bridge.core

/**
 * Разобранное BLE-уведомление с аудио (контракт протокола Omi, см.
 * `bridge/uzel_bridge/protocol.py` на стороне моста ПК).
 *
 * Каждое уведомление — 3 байта заголовка и один цельный кадр Opus:
 * собирать кадр из нескольких пакетов не нужно.
 */
data class OmiPacket(
    val packetIndex: Int,
    val internalIndex: Int,
    val payload: ByteArray,
) {
    val isEmpty: Boolean
        get() = payload.isEmpty()

    // ByteArray в data class сравнивается по ссылке — переопределяем честно.
    override fun equals(other: Any?): Boolean {
        if (this === other) return true
        if (other !is OmiPacket) return false
        return packetIndex == other.packetIndex &&
            internalIndex == other.internalIndex &&
            payload.contentEquals(other.payload)
    }

    override fun hashCode(): Int {
        var result = packetIndex
        result = 31 * result + internalIndex
        result = 31 * result + payload.contentHashCode()
        return result
    }
}

/** Размер заголовка BLE-уведомления: uint16 LE номер пакета + uint8 индекс. */
const val PACKET_HEADER_BYTES = 3

/** Счётчик пакетов на кулоне — uint16, переполняется через это значение. */
const val PACKET_COUNTER_MODULO = 1 shl 16

/**
 * Разбирает BLE-уведомление. `null`, если данных нет или пакет короче
 * заголовка (меньше 4 байт — заголовок плюс хотя бы один байт кадра).
 */
fun parsePacket(data: ByteArray?): OmiPacket? {
    if (data == null || data.size <= PACKET_HEADER_BYTES) return null
    // uint16 LE без знака: старший бит первого полубайта не должен уезжать в знак.
    val packetIndex = (data[0].toInt() and 0xFF) or ((data[1].toInt() and 0xFF) shl 8)
    val internalIndex = data[2].toInt() and 0xFF
    return OmiPacket(
        packetIndex = packetIndex,
        internalIndex = internalIndex,
        payload = data.copyOfRange(PACKET_HEADER_BYTES, data.size),
    )
}
