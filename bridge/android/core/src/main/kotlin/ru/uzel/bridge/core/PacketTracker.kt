package ru.uzel.bridge.core

/**
 * Считает потери по счётчику пакетов с учётом переполнения uint16.
 *
 * Поведение повторяет `PacketTracker` из `bridge/uzel_bridge/protocol.py`:
 * подряд идущие номера и повтор одного номера потерей не считаются,
 * переход 65535 -> 0 — штатное переполнение, а не потеря.
 */
class PacketTracker {
    /** Сколько пакетов дошло. */
    var received: Int = 0
        private set

    /** Сколько пакетов потеряно по пропускам номеров. */
    var lost: Int = 0
        private set

    /** Сколько байт полезной нагрузки дошло. */
    var payloadBytes: Long = 0L
        private set

    private var lastIndex: Int? = null

    /** Учитывает пакет и возвращает, сколько пакетов потеряно перед ним. */
    fun observe(packet: OmiPacket): Int {
        received++
        payloadBytes += packet.payload.size
        val last = lastIndex
        lastIndex = packet.packetIndex
        if (last == null) return 0

        // Разность по модулю 65536: в Kotlin % может дать отрицательное,
        // поэтому добавляем модуль перед взятием остатка.
        val rawGap = (packet.packetIndex - last) % PACKET_COUNTER_MODULO
        val gap = if (rawGap < 0) rawGap + PACKET_COUNTER_MODULO else rawGap
        if (gap <= 1) return 0 // gap == 0 — повтор того же номера, не потеря

        val lostNow = gap - 1
        lost += lostNow
        return lostNow
    }

    /**
     * Сброс при переподключении: счётчик на устройстве начинается заново,
     * поэтому следующий пакет не сравнивается с номером до разрыва.
     */
    fun reset() {
        lastIndex = null
    }

    /** Доля потерь: lost / (received + lost), 0.0 пока данных нет. */
    val lossRatio: Double
        get() {
            val total = received + lost
            return if (total == 0) 0.0 else lost.toDouble() / total
        }
}
