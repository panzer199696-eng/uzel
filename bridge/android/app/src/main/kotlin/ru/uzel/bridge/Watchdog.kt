package ru.uzel.bridge

import android.app.AlarmManager
import android.app.PendingIntent
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.os.Build
import android.os.SystemClock
import android.util.Log
import ru.uzel.bridge.core.BridgeSnapshot
import java.io.File

/**
 * Сторож: поднимает службу, если она должна работать ([Prefs.autoStart]),
 * а её убили. ColorOS при смахивании из «Недавних» бьёт процесс сигналом 9
 * и запрещает системе перезапуск (`OplusAppStartupManager: prevent restart
 * service` — поймано 23.10.2026), поэтому START_STICKY там бесполезен.
 *
 * Будильник точный и «сквозь сон»: только такой даёт приложению право
 * поднять службу переднего плана из фона. Цепочка: каждый срабатывший
 * будильник ставит следующий.
 */
object Watchdog {

    private const val TAG = "UzelWatchdog"
    private const val REQUEST = 7

    /** Обычный обход — раз в 5 минут. */
    const val PERIOD_MS = 5 * 60_000L

    fun schedule(context: Context, delayMs: Long = PERIOD_MS) {
        val am = context.getSystemService(AlarmManager::class.java) ?: return
        val pi = pendingIntent(context)
        val at = SystemClock.elapsedRealtime() + delayMs
        try {
            if (Build.VERSION.SDK_INT < Build.VERSION_CODES.S || am.canScheduleExactAlarms()) {
                am.setExactAndAllowWhileIdle(AlarmManager.ELAPSED_REALTIME_WAKEUP, at, pi)
            } else {
                am.setAndAllowWhileIdle(AlarmManager.ELAPSED_REALTIME_WAKEUP, at, pi)
            }
        } catch (e: SecurityException) {
            am.setAndAllowWhileIdle(AlarmManager.ELAPSED_REALTIME_WAKEUP, at, pi)
        }
    }

    fun cancel(context: Context) {
        context.getSystemService(AlarmManager::class.java)?.cancel(pendingIntent(context))
    }

    /** Служба жива, если недавно писала снимок состояния. */
    fun serviceAlive(context: Context): Boolean =
        BridgeSnapshot.readFrom(snapshotFile(context)).isFresh(System.currentTimeMillis())

    fun snapshotFile(context: Context) = File(context.filesDir, "bridge-state.json")

    /** Поднять службу, если должна работать и не работает. */
    fun ensureRunning(context: Context, reason: String) {
        if (!Prefs(context).autoStart) return
        schedule(context)
        if (serviceAlive(context)) return
        Log.i(TAG, "служба не отвечает — поднимаю ($reason)")
        try {
            RecorderService.start(context)
        } catch (e: Exception) {
            // Android не дал стартовать из фона — попробуем на следующем обходе.
            Log.w(TAG, "не дали поднять службу: $e")
        }
    }

    private fun pendingIntent(context: Context): PendingIntent =
        PendingIntent.getBroadcast(
            context,
            REQUEST,
            Intent(context, WatchdogReceiver::class.java),
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT,
        )
}

/** Будильник сторожа, загрузка телефона, обновление приложения. */
class WatchdogReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        Watchdog.ensureRunning(context, intent.action ?: "будильник")
    }
}
