package ru.uzel.bridge

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent

/**
 * Поднимает службу после перезагрузки телефона и после обновления самого
 * приложения (установка поверх убивает процесс), если мост был включён.
 */
class BootReceiver : BroadcastReceiver() {

    override fun onReceive(context: Context, intent: Intent) {
        when (intent.action) {
            Intent.ACTION_BOOT_COMPLETED,
            Intent.ACTION_MY_PACKAGE_REPLACED,
            -> Watchdog.ensureRunning(context, intent.action ?: "")
        }
    }
}
