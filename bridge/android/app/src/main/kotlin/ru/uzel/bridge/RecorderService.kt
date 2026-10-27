package ru.uzel.bridge

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
import android.os.Build
import android.os.Handler
import android.os.HandlerThread
import android.os.IBinder
import android.os.Looper
import android.util.Log
import ru.uzel.bridge.core.BridgeSnapshot
import ru.uzel.bridge.core.CoreClient
import ru.uzel.bridge.core.DiskSpool
import ru.uzel.bridge.core.OpusFrameBuffer
import ru.uzel.bridge.core.SendResult
import ru.uzel.bridge.core.Uploader
import java.io.File

/**
 * Служба переднего плана: держит соединение с кулоном, копит кадры Opus,
 * раз в 5 секунд сливает буфер и досылает спул, раз в 30 секунд
 * рассказывает ядру о состоянии по `/device/status`, раз в 2 секунды
 * пишет снимок состояния для экрана ([BridgeSnapshot]).
 *
 * Уведомление обязательное для службы переднего плана, но в шторке оно не
 * нужно (решение автора 23.10.2026): канал минимальной важности, без
 * значка и звука. Без разрешения на уведомления (Android 13+) служба
 * работает так же, уведомление просто не показывается.
 */
class RecorderService : Service() {

    private lateinit var prefs: Prefs
    private lateinit var client: CoreClient
    private lateinit var spool: DiskSpool
    private lateinit var uploader: Uploader
    private lateinit var buffer: OpusFrameBuffer
    private lateinit var snapshotFile: File
    private var ble: BleClient? = null

    /** Сетевые операции — на своём потоке, main занят уведомлениями. */
    private lateinit var netThread: HandlerThread
    private lateinit var netHandler: Handler
    // Looper.getMainLooper(), а НЕ mainLooper: поле службы вычисляется в
    // конструкторе, когда Context ещё не присоединён (attach() идёт позже),
    // и обращение к ContextWrapper.getMainLooper() роняет службу с NPE.
    private val main = Handler(Looper.getMainLooper())

    @Volatile private var connected = false
    @Volatile private var codecName: String? = null
    @Volatile private var batteryPercent: Int? = null
    @Volatile private var running = false
    @Volatile private var lastFrameAt = 0L
    @Volatile private var lastSentAt = 0L
    private var startedAt = 0L

    private val flushTick = object : Runnable {
        override fun run() {
            if (!running) return
            netHandler.post { flushOnce() }
            main.postDelayed(this, FLUSH_INTERVAL_MS)
        }
    }

    private val statusTick = object : Runnable {
        override fun run() {
            if (!running) return
            netHandler.post { reportStatusOnce() }
            main.postDelayed(this, STATUS_INTERVAL_MS)
        }
    }

    private val snapshotTick = object : Runnable {
        override fun run() {
            if (!running) return
            writeSnapshot()
            main.postDelayed(this, SNAPSHOT_INTERVAL_MS)
        }
    }

    private val bleListener = object : BleClient.Listener {
        // Колбэки приходят из BLE-стека; буфер потокобезопасен,
        // остальное — только volatile-флаги.
        override fun onAudioFrame(frame: ByteArray) {
            buffer.add(frame)
            lastFrameAt = System.currentTimeMillis()
        }

        override fun onConnectionState(connected: Boolean) {
            this@RecorderService.connected = connected
            main.post { updateNotification() }
        }

        override fun onCodec(codecName: String?) {
            this@RecorderService.codecName = codecName
        }

        override fun onBatteryLevel(percent: Int?) {
            batteryPercent = percent
        }

        override fun onLog(message: String) {
            Log.d(TAG, "кулон: $message")
        }
    }

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onCreate() {
        super.onCreate()
        prefs = Prefs(this)
        client = CoreClient(prefs.coreUrl, prefs.uid, token = prefs.apiToken)
        spool = DiskSpool(File(filesDir, "spool"))
        uploader = Uploader(client, spool)
        buffer = OpusFrameBuffer()
        snapshotFile = Watchdog.snapshotFile(this)
        netThread = HandlerThread("uzel-recorder-net").apply { start() }
        netHandler = Handler(netThread.looper)
        createChannel()
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        when (intent?.action) {
            ACTION_STOP -> {
                stopEverything()
                return START_NOT_STICKY
            }
            else -> startRecording()
        }
        return START_STICKY
    }

    private fun startRecording() {
        if (running) {
            updateNotification()
            return
        }
        running = true
        startedAt = System.currentTimeMillis()
        startForegroundCompat(buildNotification())
        ble = BleClient(this, bleListener).also { it.start() }
        main.postDelayed(flushTick, FLUSH_INTERVAL_MS)
        main.postDelayed(statusTick, STATUS_INTERVAL_MS)
        main.post(snapshotTick)
        Watchdog.schedule(this)
    }

    /** Слив буфера и досылка спула — только на сетевом потоке. */
    private fun flushOnce() {
        val payload = buffer.drain()
        if (payload != null) {
            if (uploader.submit(payload, System.currentTimeMillis()) == SendResult.ACCEPTED) {
                lastSentAt = System.currentTimeMillis()
            }
        }
        if (uploader.flush() > 0) lastSentAt = System.currentTimeMillis()
        main.post { updateNotification() }
    }

    private fun reportStatusOnce() {
        val stats = ble?.packetStats()
        val ok = client.reportStatus(
            batteryPercent = batteryPercent,
            codec = codecName,
            connected = connected,
            packets = stats?.received?.toLong() ?: 0L,
            lost = stats?.lost?.toLong() ?: 0L,
            spooled = spool.count(),
        )
        if (!ok) Log.d(TAG, "отчёт о состоянии не дошёл до ядра")
    }

    private fun writeSnapshot(isRunning: Boolean = running) {
        val stats = ble?.packetStats()
        try {
            BridgeSnapshot(
                running = isRunning,
                connected = connected,
                batteryPercent = batteryPercent,
                codec = codecName,
                packets = stats?.received ?: 0,
                lost = stats?.lost ?: 0,
                sent = uploader.sent,
                spooled = spool.count(),
                muted = uploader.muted,
                lastFrameAt = lastFrameAt,
                startedAt = startedAt,
                lastSentAt = lastSentAt,
                writtenAt = System.currentTimeMillis(),
            ).writeTo(snapshotFile)
        } catch (e: Exception) {
            Log.w(TAG, "снимок состояния не записан: $e")
        }
    }

    private fun stopEverything() {
        running = false
        Watchdog.cancel(this)
        main.removeCallbacks(flushTick)
        main.removeCallbacks(statusTick)
        main.removeCallbacks(snapshotTick)
        ble?.stop()
        ble = null
        connected = false
        writeSnapshot(isRunning = false)
        // Финальный слив — после него можно умирать.
        netHandler.post {
            buffer.drain()?.let { uploader.submit(it, System.currentTimeMillis()) }
            uploader.flush()
            main.post {
                stopForeground(STOP_FOREGROUND_REMOVE)
                stopSelf()
            }
        }
    }

    /**
     * Карточку смахнули из «Недавних». На чистом Android служба переживает
     * это сама; на ColorOS процесс убивают следом — ставим сторожа на
     * ближайшие секунды, вдруг система даст подняться.
     */
    override fun onTaskRemoved(rootIntent: Intent?) {
        super.onTaskRemoved(rootIntent)
        if (prefs.autoStart) Watchdog.schedule(this, delayMs = 3_000)
    }

    override fun onDestroy() {
        if (running) writeSnapshot(isRunning = false)
        running = false
        main.removeCallbacksAndMessages(null)
        ble?.stop()
        netThread.quitSafely()
        super.onDestroy()
    }

    // ---- Уведомление ----

    private fun createChannel() {
        val nm = getSystemService(NotificationManager::class.java)
        // Канал 0.1.0 был обычной важности — убираем, чтобы не висел в шторке.
        nm.deleteNotificationChannel(OLD_CHANNEL_ID)
        nm.createNotificationChannel(
            NotificationChannel(
                CHANNEL_ID,
                getString(R.string.notification_channel),
                NotificationManager.IMPORTANCE_MIN,
            ).apply {
                setShowBadge(false)
                setSound(null, null)
                enableVibration(false)
                lockscreenVisibility = Notification.VISIBILITY_SECRET
            }
        )
    }

    private fun startForegroundCompat(notification: Notification) {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
            startForeground(NOTE_ID, notification, ServiceInfo.FOREGROUND_SERVICE_TYPE_CONNECTED_DEVICE)
        } else {
            startForeground(NOTE_ID, notification)
        }
    }

    private fun buildNotification(): Notification {
        val intent = PendingIntent.getActivity(
            this,
            0,
            Intent(this, MainActivity::class.java),
            PendingIntent.FLAG_IMMUTABLE,
        )
        val state = if (connected) {
            getString(R.string.state_connected)
        } else {
            getString(R.string.state_disconnected)
        }
        return Notification.Builder(this, CHANNEL_ID)
            .setSmallIcon(R.drawable.ic_stat_uzel)
            .setContentTitle(getString(R.string.notification_title))
            .setContentText(state)
            .setContentIntent(intent)
            .setOnlyAlertOnce(true)
            .setShowWhen(false)
            .setVisibility(Notification.VISIBILITY_SECRET)
            .setCategory(Notification.CATEGORY_SERVICE)
            .build()
    }

    private var lastNotifiedConnected: Boolean? = null

    /** Текст уведомления меняется только при смене связи — без дёрганья раз в 5 с. */
    private fun updateNotification() {
        if (lastNotifiedConnected == connected) return
        lastNotifiedConnected = connected
        val nm = getSystemService(NotificationManager::class.java)
        try {
            nm.notify(NOTE_ID, buildNotification())
        } catch (e: SecurityException) {
            // Уведомления запрещены — служба работает и без них.
        }
    }

    companion object {
        const val ACTION_START = "ru.uzel.bridge.action.START"
        const val ACTION_STOP = "ru.uzel.bridge.action.STOP"
        private const val TAG = "RecorderService"
        private const val OLD_CHANNEL_ID = "uzel_recorder"
        private const val CHANNEL_ID = "uzel_quiet"
        private const val NOTE_ID = 42
        private const val FLUSH_INTERVAL_MS = 5_000L
        private const val STATUS_INTERVAL_MS = 30_000L
        private const val SNAPSHOT_INTERVAL_MS = 2_000L

        fun start(context: Context) {
            val intent = Intent(context, RecorderService::class.java).setAction(ACTION_START)
            context.startForegroundService(intent)
        }

        fun stop(context: Context) {
            val intent = Intent(context, RecorderService::class.java).setAction(ACTION_STOP)
            try {
                context.startService(intent)
            } catch (e: Exception) {
                // Служба ещё не запускалась — останавливать нечего.
            }
        }
    }
}
