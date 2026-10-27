package ru.uzel.bridge

import android.Manifest
import android.annotation.SuppressLint
import android.content.ComponentName
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.net.Uri
import android.os.Build
import android.os.PowerManager
import android.provider.Settings
import ru.uzel.bridge.core.Note
import ru.uzel.bridge.core.NotesStore
import java.io.File

/**
 * Разрешения, без которых мост не работает: Bluetooth (12+), местоположение
 * (до 12), доступ к локальной сети (17+) — ядро живёт на ПК в домашней сети.
 */
fun requiredPermissions(): List<String> = buildList {
    if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
        add(Manifest.permission.BLUETOOTH_SCAN)
        add(Manifest.permission.BLUETOOTH_CONNECT)
    } else {
        add(Manifest.permission.ACCESS_FINE_LOCATION)
    }
    if (Build.VERSION.SDK_INT >= 37) add(ACCESS_LOCAL_NETWORK)
}

const val ACCESS_LOCAL_NETWORK = "android.permission.ACCESS_LOCAL_NETWORK"

fun Context.missingPermissions(): List<String> =
    requiredPermissions().filter { checkSelfPermission(it) != PackageManager.PERMISSION_GRANTED }

/** Заметки живут в одном файле на всё приложение. */
fun Context.notesStore(): NotesStore = NotesStore(File(filesDir, "notes.json"))

/** Метка момента: из приложения, плитки в шторке или ярлыка. */
fun Context.addMoment(text: String = ""): Note = notesStore().add(text, moment = true)

/** Что мешает мосту жить в фоне — для экрана «Работа в фоне». */
object Keepalive {

    fun ignoresBatteryOptimizations(context: Context): Boolean =
        context.getSystemService(PowerManager::class.java)
            ?.isIgnoringBatteryOptimizations(context.packageName) == true

    /** Системный диалог «Разрешить работу в фоне без ограничений». */
    @SuppressLint("BatteryLife")
    fun batteryIntent(context: Context): Intent =
        Intent(Settings.ACTION_REQUEST_IGNORE_BATTERY_OPTIMIZATIONS)
            .setData(Uri.parse("package:" + context.packageName))

    fun appDetailsIntent(context: Context): Intent =
        Intent(Settings.ACTION_APPLICATION_DETAILS_SETTINGS)
            .setData(Uri.parse("package:" + context.packageName))

    fun notificationSettingsIntent(context: Context): Intent =
        Intent(Settings.ACTION_APP_NOTIFICATION_SETTINGS)
            .putExtra(Settings.EXTRA_APP_PACKAGE, context.packageName)

    /**
     * Страница ColorOS «Расход батареи» приложения — там «Разрешить
     * фоновую активность» и автозапуск. Активности у прошивок разные,
     * поэтому перебор; не нашлась ни одна — общая карточка приложения.
     */
    fun colorOsBatteryIntent(context: Context): Intent {
        val pkg = context.packageName
        val candidates = listOf(
            ComponentName("com.oplus.battery", "com.oplus.powermanager.fuelgaue.PowerAppsBgSetting"),
            ComponentName("com.oplus.battery", "com.oplus.powermanager.fuelgaue.PowerControlActivity"),
            ComponentName("com.coloros.oppoguardelf", "com.coloros.powermanager.fuelgaue.PowerUsageModelActivity"),
        )
        for (cn in candidates) {
            val intent = Intent()
                .setComponent(cn)
                .putExtra("pkgName", pkg)
                .putExtra("package_name", pkg)
                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
            if (intent.resolveActivity(context.packageManager) != null) return intent
        }
        return appDetailsIntent(context)
    }

    fun canPostNotifications(context: Context): Boolean =
        Build.VERSION.SDK_INT < Build.VERSION_CODES.TIRAMISU ||
            context.checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) ==
            PackageManager.PERMISSION_GRANTED
}
