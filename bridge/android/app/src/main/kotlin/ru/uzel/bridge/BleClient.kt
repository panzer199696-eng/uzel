package ru.uzel.bridge

import android.Manifest
import android.annotation.SuppressLint
import android.bluetooth.BluetoothAdapter
import android.bluetooth.BluetoothDevice
import android.bluetooth.BluetoothGatt
import android.bluetooth.BluetoothGattCallback
import android.bluetooth.BluetoothGattCharacteristic
import android.bluetooth.BluetoothGattDescriptor
import android.bluetooth.BluetoothManager
import android.bluetooth.BluetoothProfile
import android.bluetooth.le.ScanCallback
import android.bluetooth.le.ScanFilter
import android.bluetooth.le.ScanResult
import android.bluetooth.le.ScanSettings
import android.content.Context
import android.content.pm.PackageManager
import android.os.Build
import android.os.Handler
import android.os.HandlerThread
import android.os.ParcelUuid
import ru.uzel.bridge.core.OmiPacket
import ru.uzel.bridge.core.PacketTracker
import ru.uzel.bridge.core.parsePacket
import java.util.UUID

/** Статистика приёма — для отчёта в `/device/status`. */
data class PacketStats(
    val received: Int,
    val lost: Int,
    val lossRatio: Double,
)

/**
 * BLE-клиент кулона Omi/Friend: сканирование по UUID сервиса и по имени,
 * подключение, подписка на аудио-характеристику и уровень батареи,
 * чтение кодека, переподключение с растущей паузой (1, 2, 4, 8 … максимум 30 с).
 *
 * Реальное радио проверить без телефона нельзя (P8), поэтому класс собран
 * defensively: ошибки уходят в [Listener.onLog], а не в падение процесса.
 * Колбэки приходят из потоков BLE-стека — слушатель сам заботится о своих.
 */
class BleClient(
    private val context: Context,
    private val listener: Listener,
) {

    interface Listener {
        /** Пришёл цельный кадр Opus из уведомления. */
        fun onAudioFrame(frame: ByteArray) {}

        /** Состояние соединения с кулоном. */
        fun onConnectionState(connected: Boolean) {}

        /** Прочитанное значение характеристики кодека. */
        fun onCodec(codecName: String?) {}

        /** Уровень батареи кулона в процентах. */
        fun onBatteryLevel(percent: Int?) {}

        /** Диагностическое сообщение для журнала. */
        fun onLog(message: String) {}
    }

    companion object {
        // Контракт протокола Omi — см. bridge/uzel_bridge/protocol.py.
        val SERVICE_UUID: UUID = UUID.fromString("19b10000-e8f2-537e-4f6c-d104768a1214")
        val AUDIO_DATA_UUID: UUID = UUID.fromString("19b10001-e8f2-537e-4f6c-d104768a1214")
        val AUDIO_CODEC_UUID: UUID = UUID.fromString("19b10002-e8f2-537e-4f6c-d104768a1214")
        val BATTERY_SERVICE_UUID: UUID = UUID.fromString("0000180f-0000-1000-8000-00805f9b34fb")
        val BATTERY_LEVEL_UUID: UUID = UUID.fromString("00002a19-0000-1000-8000-00805f9b34fb")
        val CCC_DESCRIPTOR_UUID: UUID = UUID.fromString("00002902-0000-1000-8000-00805f9b34fb")

        /** Имена, под которыми кулон рекламируется в эфире. «uzel» — своя прошивка с 0.1.0. */
        val DEVICE_NAME_HINTS = listOf("omi", "friend", "uzel")

        /**
         * Отказы GATT из-за отсутствия шифрования: прошивка УЗЛА (0.1.0+) даёт
         * подписаться на звук только сопряжённому телефону. 5 — insufficient
         * authentication, 15 — insufficient encryption, 137 — GATT_AUTH_FAIL.
         */
        private val AUTH_STATUSES = setOf(5, 15, 137)
        private const val BOND_WAIT_MS = 30_000L
        private const val DIRECT_CONNECT_AFTER_MS = 10_000L

        /** Значения характеристики кодека (сверено с protocol.py). */
        val CODEC_NAMES: Map<Int, String> = mapOf(
            0 to "PCM 16 бит 16 кГц",
            1 to "PCM 8 бит 16 кГц",
            10 to "μ-law 8 бит 16 кГц",
            20 to "Opus 16 кГц",
            21 to "Opus 16 кГц (FEC)",
        )

        private const val RECONNECT_MIN_MS = 1_000L
        private const val RECONNECT_MAX_MS = 30_000L
    }

    private val tracker = PacketTracker()

    @Volatile private var wantRunning = false
    @Volatile private var gatt: BluetoothGatt? = null
    @Volatile private var scanning = false
    private var reconnectDelayMs = RECONNECT_MIN_MS

    /** BLE-операции сериализуются на собственном потоке. */
    private val thread = HandlerThread("uzel-ble").apply { start() }
    private val handler = Handler(thread.looper)

    private val adapter: BluetoothAdapter?
        get() = (context.getSystemService(Context.BLUETOOTH_SERVICE) as? BluetoothManager)?.adapter

    private val scanCallback = object : ScanCallback() {
        override fun onScanResult(callbackType: Int, result: ScanResult) {
            val record = result.scanRecord
            val name = record?.deviceName
            val hasService = record?.serviceUuids?.contains(ParcelUuid(SERVICE_UUID)) == true
            val byName = name != null && DEVICE_NAME_HINTS.any {
                name.contains(it, ignoreCase = true)
            }
            if (hasService || byName) {
                stopScanInternal()
                connectInternal(result.device)
            }
        }

        override fun onScanFailed(errorCode: Int) {
            scanning = false
            listener.onLog("скан не запустился, код $errorCode")
            scheduleReconnect()
        }
    }

    /** Запускает поиск и подключение к кулону. */
    fun start() {
        wantRunning = true
        reconnectDelayMs = RECONNECT_MIN_MS
        if (!hasPermissions()) {
            listener.onLog("нет разрешений на Bluetooth — скан не запущен")
            return
        }
        handler.post { scanInternal() }
    }

    /** Полная остановка: скан, соединение, рабочий поток. */
    fun stop() {
        wantRunning = false
        handler.removeCallbacksAndMessages(null)
        stopScanInternal()
        closeGatt()
        thread.quitSafely()
    }

    /** Статистика приёма с момента последнего переподключения. */
    fun packetStats(): PacketStats =
        PacketStats(tracker.received, tracker.lost, tracker.lossRatio)

    @SuppressLint("MissingPermission")
    private fun scanInternal() {
        if (!wantRunning) return
        val scanner = adapter?.bluetoothLeScanner
        if (scanner == null) {
            listener.onLog("Bluetooth LE недоступен")
            scheduleReconnect()
            return
        }
        val filters = listOf(
            ScanFilter.Builder().setServiceUuid(ParcelUuid(SERVICE_UUID)).build(),
            ScanFilter.Builder().setDeviceName("omi").build(),
            ScanFilter.Builder().setDeviceName("friend").build(),
            ScanFilter.Builder().setDeviceName("UZEL").build(),
        )
        val settings = ScanSettings.Builder()
            .setScanMode(ScanSettings.SCAN_MODE_LOW_LATENCY)
            .build()
        try {
            scanner.startScan(filters, settings, scanCallback)
            scanning = true
            listener.onLog("ищем кулон…")
            handler.postDelayed({ connectKnownIfNotFound() }, DIRECT_CONNECT_AFTER_MS)
        } catch (e: SecurityException) {
            listener.onLog("скан запрещён: ${e.message}")
        } catch (e: Exception) {
            listener.onLog("скан не удался: ${e.message}")
            scheduleReconnect()
        }
    }

    /**
     * Сканирование не нашло кулон, а адрес известен — подключаемся напрямую.
     * Кулон держит одно соединение и не рекламируется, пока занят. Занять его
     * может сам Android: после остановки моста система оставляет связь с
     * сопряжённым устройством (поймано 26.10.2026), и скан не видит ничего.
     * connectGatt по адресу садится на уже существующую связь.
     */
    @SuppressLint("MissingPermission")
    private fun connectKnownIfNotFound() {
        if (!wantRunning || !scanning || gatt != null) return
        val address = Prefs(context).pendantAddress ?: return
        val device = try {
            adapter?.getRemoteDevice(address)
        } catch (e: IllegalArgumentException) {
            null
        } ?: return
        stopScanInternal()
        listener.onLog("в эфире кулона нет — подключаюсь напрямую к $address")
        connectInternal(device)
    }

    @SuppressLint("MissingPermission")
    private fun stopScanInternal() {
        if (!scanning) return
        scanning = false
        try {
            adapter?.bluetoothLeScanner?.stopScan(scanCallback)
        } catch (e: Exception) {
            listener.onLog("стоп скана: ${e.message}")
        }
    }

    /** Пауза перед повторной попыткой удваивается: 1, 2, 4, 8 … максимум 30 с. */
    private fun scheduleReconnect() {
        if (!wantRunning) return
        val delay = reconnectDelayMs
        reconnectDelayMs = (reconnectDelayMs * 2).coerceAtMost(RECONNECT_MAX_MS)
        listener.onLog("повторная попытка через ${delay / 1000} с")
        handler.postDelayed({ scanInternal() }, delay)
    }

    @SuppressLint("MissingPermission")
    private fun connectInternal(device: BluetoothDevice) {
        listener.onLog("подключаемся к ${device.address}")
        try {
            gatt = device.connectGatt(context, false, gattCallback, BluetoothDevice.TRANSPORT_LE)
        } catch (e: SecurityException) {
            listener.onLog("подключение запрещено: ${e.message}")
            scheduleReconnect()
        }
    }

    @SuppressLint("MissingPermission")
    private fun closeGatt() {
        try {
            gatt?.close()
        } catch (e: Exception) {
            // close на уже мёртвом соединении может бросить — не страшно.
        }
        gatt = null
    }

    private val gattCallback = object : BluetoothGattCallback() {
        @SuppressLint("MissingPermission")
        override fun onConnectionStateChange(g: BluetoothGatt, status: Int, newState: Int) {
            when (newState) {
                BluetoothProfile.STATE_CONNECTED -> {
                    listener.onLog("соединение установлено")
                    reconnectDelayMs = RECONNECT_MIN_MS
                    // Адрес нужен обновлению прошивки по воздуху (настройки → «Прошивка кулона»).
                    Prefs(context).pendantAddress = g.device.address
                    // Счётчик на кулоне после переподключения начинается заново.
                    tracker.reset()
                    listener.onConnectionState(true)
                    // Без согласования MTU канал остаётся 23 байта, а кадр Opus
                    // крупнее: прошивка не шлёт его вовсе (bt_gatt_notify > MTU).
                    // Службы ищем уже в onMtuChanged.
                    try {
                        if (!g.requestMtu(517)) g.discoverServices()
                    } catch (e: SecurityException) {
                        listener.onLog("обнаружение служб запрещено")
                    }
                }
                BluetoothProfile.STATE_DISCONNECTED -> {
                    listener.onLog("соединение потеряно (статус $status)")
                    listener.onConnectionState(false)
                    closeGatt()
                    scheduleReconnect()
                }
            }
        }

        @SuppressLint("MissingPermission")
        override fun onServicesDiscovered(g: BluetoothGatt, status: Int) {
            if (status != BluetoothGatt.GATT_SUCCESS) {
                listener.onLog("обнаружение служб не удалось, код $status")
                g.disconnect()
                return
            }
            val audio = g.getService(SERVICE_UUID)?.getCharacteristic(AUDIO_DATA_UUID)
            if (audio == null) {
                listener.onLog("кулон не предоставляет характеристику аудио")
                g.disconnect()
                return
            }
            // Android держит одну GATT-операцию за раз: следующую запускаем
            // из колбэка завершения предыдущей, иначе они молча теряются.
            gattOps.clear()
            gattOps.add { enableNotifications(g, audio) }
            g.getService(SERVICE_UUID)?.getCharacteristic(AUDIO_CODEC_UUID)?.let {
                gattOps.add {
                    try {
                        g.readCharacteristic(it)
                    } catch (e: SecurityException) {
                        listener.onLog("чтение кодека запрещено")
                    }
                }
            }
            g.getService(BATTERY_SERVICE_UUID)?.getCharacteristic(BATTERY_LEVEL_UUID)?.let {
                gattOps.add { enableNotifications(g, it) }
                gattOps.add {
                    try {
                        g.readCharacteristic(it)
                    } catch (e: SecurityException) {
                        listener.onLog("чтение батареи запрещено")
                    }
                }
            }
            nextGattOp()
        }

        @SuppressLint("MissingPermission")
        override fun onMtuChanged(g: BluetoothGatt, mtu: Int, status: Int) {
            listener.onLog("MTU $mtu (статус $status)")
            try {
                g.requestConnectionPriority(BluetoothGatt.CONNECTION_PRIORITY_HIGH)
                g.discoverServices()
            } catch (e: SecurityException) {
                listener.onLog("обнаружение служб запрещено")
            }
        }

        // Обе сигнатуры чтения: новая приходит на Android 13+, старая — раньше.
        override fun onCharacteristicRead(
            g: BluetoothGatt,
            characteristic: BluetoothGattCharacteristic,
            status: Int,
        ) {
            if (status == BluetoothGatt.GATT_SUCCESS) {
                @Suppress("DEPRECATION")
                handleRead(characteristic.uuid, characteristic.value)
            }
            nextGattOp()
        }

        override fun onCharacteristicRead(
            g: BluetoothGatt,
            characteristic: BluetoothGattCharacteristic,
            value: ByteArray,
            status: Int,
        ) {
            if (status == BluetoothGatt.GATT_SUCCESS) {
                handleRead(characteristic.uuid, value)
            }
            nextGattOp()
        }

        // Обе сигнатуры уведомлений — по той же причине.
        override fun onCharacteristicChanged(
            g: BluetoothGatt,
            characteristic: BluetoothGattCharacteristic,
        ) {
            @Suppress("DEPRECATION")
            handleChanged(characteristic.uuid, characteristic.value)
        }

        override fun onCharacteristicChanged(
            g: BluetoothGatt,
            characteristic: BluetoothGattCharacteristic,
            value: ByteArray,
        ) {
            handleChanged(characteristic.uuid, value)
        }

        override fun onDescriptorWrite(g: BluetoothGatt, descriptor: BluetoothGattDescriptor, status: Int) {
            if (status in AUTH_STATUSES && descriptor.characteristic.uuid == AUDIO_DATA_UUID) {
                requestBond(g, descriptor.characteristic)
                return  // очередь продолжит requestBond, когда сопряжение закончится
            }
            if (status != BluetoothGatt.GATT_SUCCESS) {
                listener.onLog("подписка на уведомления не удалась, код $status")
            }
            nextGattOp()
        }
    }

    /**
     * Кулон потребовал шифрование, а сопряжения нет. Обычно Android сам
     * показывает запрос на сопряжение и повторяет запись — сюда мы попадаем,
     * когда он этого не сделал. Сопрягаемся явно и ждём ключи до 30 с; кулон
     * принимает новое устройство, только пока лежит на зарядке (USB).
     */
    @SuppressLint("MissingPermission")
    private fun requestBond(g: BluetoothGatt, audio: BluetoothGattCharacteristic) {
        val device = g.device
        try {
            if (device.bondState == BluetoothDevice.BOND_NONE) {
                listener.onLog("кулон просит сопряжение — если не выйдет, положите его на зарядку")
                device.createBond()
            }
        } catch (e: SecurityException) {
            listener.onLog("сопряжение запрещено: ${e.message}")
            return
        }
        val deadline = System.currentTimeMillis() + BOND_WAIT_MS
        var sawBonding = false
        fun poll() {
            if (gatt !== g) return  // за это время соединение сменилось
            val state = try { device.bondState } catch (e: SecurityException) { BluetoothDevice.BOND_NONE }
            if (state == BluetoothDevice.BOND_BONDING) sawBonding = true
            val rejected = sawBonding && state == BluetoothDevice.BOND_NONE
            when {
                state == BluetoothDevice.BOND_BONDED -> {
                    listener.onLog("сопряжено, подписываемся заново")
                    gattOps.add { enableNotifications(g, audio) }
                    nextGattOp()
                }
                rejected || System.currentTimeMillis() > deadline -> {
                    listener.onLog("кулон отказал в сопряжении: положите его на зарядку (USB) и переподключите")
                    try { g.disconnect() } catch (e: SecurityException) { }
                }
                else -> handler.postDelayed({ poll() }, 1_000)
            }
        }
        handler.postDelayed({ poll() }, 1_000)
    }

    /** Очередь GATT-операций: следующая стартует из колбэка завершения предыдущей. */
    private val gattOps = java.util.concurrent.ConcurrentLinkedQueue<() -> Unit>()

    private fun nextGattOp() {
        gattOps.poll()?.invoke()
    }

    private fun handleRead(uuid: UUID, value: ByteArray?) {
        when (uuid) {
            AUDIO_CODEC_UUID -> {
                val code = value?.firstOrNull()?.toInt()
                val name = code?.let { CODEC_NAMES[it] } ?: "неизвестен"
                listener.onCodec(name)
            }
            BATTERY_LEVEL_UUID -> listener.onBatteryLevel(value?.firstOrNull()?.toInt())
        }
    }

    private fun handleChanged(uuid: UUID, value: ByteArray?) {
        if (value == null) return
        when (uuid) {
            AUDIO_DATA_UUID -> {
                val packet: OmiPacket = parsePacket(value) ?: return
                val lostNow = tracker.observe(packet)
                if (lostNow > 0) listener.onLog("потеряно пакетов: $lostNow")
                if (packet.payload.isNotEmpty()) listener.onAudioFrame(packet.payload)
            }
            BATTERY_LEVEL_UUID -> listener.onBatteryLevel(value.firstOrNull()?.toInt())
        }
    }

    /** Включает уведомления и записывает дескриптор CCC (0x2902). */
    @SuppressLint("MissingPermission")
    private fun enableNotifications(g: BluetoothGatt, characteristic: BluetoothGattCharacteristic) {
        try {
            g.setCharacteristicNotification(characteristic, true)
            val ccc = characteristic.getDescriptor(CCC_DESCRIPTOR_UUID)
            if (ccc == null) {
                listener.onLog("у характеристики нет дескриптора CCC")
                nextGattOp()  // колбэка записи не будет — не вешаем очередь
                return
            }
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
                g.writeDescriptor(ccc, BluetoothGattDescriptor.ENABLE_NOTIFICATION_VALUE)
            } else {
                @Suppress("DEPRECATION")
                ccc.value = BluetoothGattDescriptor.ENABLE_NOTIFICATION_VALUE
                @Suppress("DEPRECATION")
                g.writeDescriptor(ccc)
            }
        } catch (e: SecurityException) {
            listener.onLog("подписка запрещена: ${e.message}")
        }
    }

    private fun hasPermissions(): Boolean {
        return if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
            granted(Manifest.permission.BLUETOOTH_SCAN) &&
                granted(Manifest.permission.BLUETOOTH_CONNECT)
        } else {
            granted(Manifest.permission.ACCESS_FINE_LOCATION)
        }
    }

    private fun granted(permission: String): Boolean =
        context.checkSelfPermission(permission) == PackageManager.PERMISSION_GRANTED
}
