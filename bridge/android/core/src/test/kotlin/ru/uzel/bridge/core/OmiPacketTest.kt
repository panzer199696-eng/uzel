package ru.uzel.bridge.core

import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

/** Тесты разбора BLE-уведомлений (ТЗ P8, пункт 1). */
class OmiPacketTest {

    @Test
    fun validPacketWithIndexAbove255() {
        // 0x01 0x01 в uint16 LE = 257: проверяем чтение без знака.
        val data = byteArrayOf(0x01, 0x01, 0x07, 0x0A, 0x0B, 0x0C)
        val packet = parsePacket(data)!!
        assertEquals(257, packet.packetIndex)
        assertEquals(7, packet.internalIndex)
        assertArrayEquals(byteArrayOf(0x0A, 0x0B, 0x0C), packet.payload)
    }

    @Test
    fun maxUint16IndexParsed() {
        // 0xFF 0xFF = 65535 — старший бит не должен уезжать в знак.
        val packet = parsePacket(byteArrayOf(0xFF.toByte(), 0xFF.toByte(), 0x00, 0x01))!!
        assertEquals(65535, packet.packetIndex)
        assertEquals(0, packet.internalIndex)
    }

    @Test
    fun exactlyThreeBytesIsNull() {
        // Пакет ровно из 3 байт — только заголовок, кадра нет.
        assertNull(parsePacket(byteArrayOf(0x05, 0x00, 0x01)))
    }

    @Test
    fun nullInputIsNull() {
        assertNull(parsePacket(null))
    }
}
