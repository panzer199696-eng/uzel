package ru.uzel.bridge.core

import org.junit.Assert.assertEquals
import org.junit.Test

/** Тесты счётчика потерь (ТЗ P8, пункт 2; поведение как в protocol.py). */
class PacketTrackerTest {

    private fun packet(index: Int, payloadSize: Int = 4): OmiPacket =
        OmiPacket(index, 0, ByteArray(payloadSize))

    @Test
    fun consecutiveNumbersNoLoss() {
        val tracker = PacketTracker()
        assertEquals(0, tracker.observe(packet(1)))
        assertEquals(0, tracker.observe(packet(2)))
        assertEquals(0, tracker.observe(packet(3)))
        assertEquals(3, tracker.received)
        assertEquals(0, tracker.lost)
        assertEquals(12L, tracker.payloadBytes)
    }

    @Test
    fun gapOfThreeCountsThreeLost() {
        val tracker = PacketTracker()
        tracker.observe(packet(10))
        assertEquals(3, tracker.observe(packet(14)))
        assertEquals(3, tracker.lost)
    }

    @Test
    fun wraparound65535To0IsNotLoss() {
        val tracker = PacketTracker()
        tracker.observe(packet(65535))
        assertEquals(0, tracker.observe(packet(0)))
        assertEquals(0, tracker.lost)
    }

    @Test
    fun duplicateNumberIsNotLoss() {
        val tracker = PacketTracker()
        tracker.observe(packet(5))
        assertEquals(0, tracker.observe(packet(5)))
        assertEquals(2, tracker.received)
        assertEquals(0, tracker.lost)
    }

    @Test
    fun resetStartsCountingFresh() {
        val tracker = PacketTracker()
        tracker.observe(packet(100))
        tracker.reset()
        // После переподключения номер начинается заново — пропуском не считается.
        assertEquals(0, tracker.observe(packet(0)))
        assertEquals(0, tracker.lost)
    }

    @Test
    fun lossRatioComputedOverTotal() {
        val tracker = PacketTracker()
        // 11 принятых, 2 потеряно: 2/13.
        for (i in 1..10) tracker.observe(packet(i))
        tracker.observe(packet(13))
        assertEquals(2.0 / 13.0, tracker.lossRatio, 1e-9)
    }

    @Test
    fun lossRatioZeroWithoutData() {
        assertEquals(0.0, PacketTracker().lossRatio, 0.0)
    }
}
