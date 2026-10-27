package ru.uzel.bridge.core

/**
 * Связка клиента и спула: пробует отправить сразу, при недоступности ядра
 * копит на диск и досылает по восстановлении, в исходном порядке.
 *
 * Ответ 204 («приватность») означает «данные не нужны»: фрагмент
 * выбрасывается, а не копится и не досылается. Это не ошибка.
 */
class Uploader(
    private val client: CoreClient,
    private val spool: DiskSpool,
) {
    /** Сколько фрагментов ядро приняло. */
    var sent: Int = 0
        private set

    /** Сколько фрагментов легло в спул. */
    var spooled: Int = 0
        private set

    /** Сколько фрагментов выброшено как испорченные (REJECTED). */
    var dropped: Int = 0
        private set

    /** Сколько фрагментов выброшено из-за приватности (204). */
    var muted: Int = 0
        private set

    /** Отправляет фрагмент; при FAILED кладёт в спул и возвращает результат. */
    fun submit(payload: ByteArray, deviceTimeMs: Long? = null): SendResult {
        val result = client.sendAudio(payload, deviceTimeMs)
        when (result) {
            SendResult.ACCEPTED -> sent++
            SendResult.FAILED -> {
                spool.add(payload, SpoolMeta(deviceTimeMs))
                spooled++
            }
            SendResult.MUTED -> muted++ // приватность: данные не нужны
            SendResult.REJECTED -> dropped++ // битый формат: повтор не поможет
        }
        return result
    }

    /**
     * Досылает накопленное (не больше `limit` за раз). Успешные удаляются.
     * На первом FAILED досылка прекращается: порядок важнее скорости.
     * Возвращает число доставленных фрагментов.
     */
    fun flush(limit: Int = 20): Int {
        var delivered = 0
        for (chunk in spool.pending(limit)) {
            when (client.sendAudio(chunk.read(), chunk.deviceTimeMs)) {
                SendResult.ACCEPTED -> {
                    spool.drop(chunk)
                    sent++
                    delivered++
                }
                SendResult.MUTED -> {
                    spool.drop(chunk)
                    muted++
                }
                SendResult.REJECTED -> {
                    spool.drop(chunk)
                    dropped++
                }
                SendResult.FAILED -> return delivered // ядро снова недоступно
            }
        }
        return delivered
    }
}
