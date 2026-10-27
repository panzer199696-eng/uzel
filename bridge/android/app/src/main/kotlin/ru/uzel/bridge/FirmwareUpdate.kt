package ru.uzel.bridge

import android.app.Activity
import android.content.Context
import android.net.Uri
import no.nordicsemi.android.dfu.DfuBaseService
import no.nordicsemi.android.dfu.DfuProgressListenerAdapter
import no.nordicsemi.android.dfu.DfuServiceInitiator
import no.nordicsemi.android.dfu.DfuServiceListenerHelper

/** Служба библиотеки Nordic DFU: сама держит соединение и показывает прогресс. */
class DfuService : DfuBaseService() {
    override fun getNotificationTarget(): Class<out Activity> = MainActivity::class.java
    override fun isDebug(): Boolean = false
}

/**
 * Обновление прошивки кулона по воздуху (Nordic Legacy DFU через загрузчик Adafruit).
 *
 * Пакет — zip из `firmware/out/uzel-fw-<версия>-dfu.zip` (build.sh). Прошивка
 * УЗЛА по команде уходит в загрузчик, а тот рекламируется с адресом +1 в младшем
 * байте — поэтому принудительный поиск нового адреса. Команда принимается только
 * от сопряжённого телефона: ключи сохраняем ([DfuServiceInitiator.setKeepBond]).
 */
class FirmwareUpdate(private val context: Context, private val onState: (String) -> Unit) {

    private val listener = object : DfuProgressListenerAdapter() {
        override fun onDeviceConnecting(deviceAddress: String) = onState("подключаюсь к кулону…")
        override fun onEnablingDfuMode(deviceAddress: String) = onState("кулон уходит в загрузчик…")
        override fun onDfuProcessStarting(deviceAddress: String) = onState("загрузчик готов, начинаю заливку…")
        override fun onProgressChanged(
            deviceAddress: String, percent: Int, speed: Float, avgSpeed: Float,
            currentPart: Int, partsTotal: Int,
        ) = onState("заливка: $percent %")
        override fun onFirmwareValidating(deviceAddress: String) = onState("кулон проверяет образ…")
        override fun onDfuCompleted(deviceAddress: String) {
            onState("готово: кулон перезапущен с новой прошивкой")
            finish()
        }
        override fun onDfuAborted(deviceAddress: String) {
            onState("обновление прервано")
            finish()
        }
        override fun onError(deviceAddress: String, error: Int, errorType: Int, message: String?) {
            onState("ошибка $error: ${message ?: "без описания"}. Если кулон не вернулся — выключите и включите его")
            finish()
        }
    }

    var onFinished: (() -> Unit)? = null

    private fun finish() {
        DfuServiceListenerHelper.unregisterProgressListener(context, listener)
        onFinished?.invoke()
    }

    fun start(address: String, zip: Uri) {
        DfuServiceInitiator.createDfuNotificationChannel(context)
        DfuServiceListenerHelper.registerProgressListener(context, listener)
        DfuServiceInitiator(address)
            .setDeviceName("UZEL")
            .setKeepBond(true)
            .setForceScanningForNewAddressInLegacyDfu(true)
            .setPacketsReceiptNotificationsEnabled(true)
            .setPacketsReceiptNotificationsValue(10)
            // Загрузчик Adafruit соглашается на MTU 247, но приём образа кусками по
            // 244 байта обрывает с OPERATION_FAILED (проверено 26.10.2026 на 0.11.0).
            // Без запроса MTU куски по 20 байт — так же, как шлёт мост с ПК, и это работает.
            .disableMtuRequest()
            .setZip(zip)
            .start(context, DfuService::class.java)
        onState("запуск обновления…")
    }
}
