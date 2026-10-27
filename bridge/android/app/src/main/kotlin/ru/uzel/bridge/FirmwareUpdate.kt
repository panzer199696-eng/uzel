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

    private fun say(id: Int, vararg args: Any) = onState(context.getString(id, *args))

    private val listener = object : DfuProgressListenerAdapter() {
        override fun onDeviceConnecting(deviceAddress: String) = say(R.string.fw_connecting)
        override fun onEnablingDfuMode(deviceAddress: String) = say(R.string.fw_dfu_mode)
        override fun onDfuProcessStarting(deviceAddress: String) = say(R.string.fw_starting)
        override fun onProgressChanged(
            deviceAddress: String, percent: Int, speed: Float, avgSpeed: Float,
            currentPart: Int, partsTotal: Int,
        ) = say(R.string.fw_progress, percent)
        override fun onFirmwareValidating(deviceAddress: String) = say(R.string.fw_validating)
        override fun onDfuCompleted(deviceAddress: String) {
            say(R.string.fw_done)
            finish()
        }
        override fun onDfuAborted(deviceAddress: String) {
            say(R.string.fw_aborted)
            finish()
        }
        override fun onError(deviceAddress: String, error: Int, errorType: Int, message: String?) {
            say(R.string.fw_error, error, message ?: context.getString(R.string.fw_no_description))
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
        say(R.string.fw_launching)
    }
}
