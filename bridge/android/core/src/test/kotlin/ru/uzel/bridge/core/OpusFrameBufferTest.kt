package ru.uzel.bridge.core

import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/** Тесты буфера кадров (ТЗ P8, пункт 3). */
class OpusFrameBufferTest {

    @Test
    fun drainOfEmptyBufferIsNull() {
        assertNull(OpusFrameBuffer().drain())
    }

    @Test
    fun roundTripThreeFramesReturnsSameBytes() {
        val buffer = OpusFrameBuffer()
        val first = byteArrayOf(1, 2, 3)
        val second = byteArrayOf(4)
        val third = ByteArray(300) { it.toByte() }
        buffer.add(first)
        buffer.add(second)
        buffer.add(third)

        val body = buffer.drain()!!
        val frames = unpackFrames(body)
        assertEquals(3, frames.size)
        assertArrayEquals(first, frames[0])
        assertArrayEquals(second, frames[1])
        assertArrayEquals(third, frames[2])
        // После drain буфер пуст.
        assertNull(buffer.drain())
    }

    @Test
    fun emptyFrameIgnored() {
        val buffer = OpusFrameBuffer()
        buffer.add(ByteArray(0))
        assertEquals(0, buffer.frameCount)
        assertNull(buffer.drain())
    }

    @Test
    fun oversizedFrameDroppedAndCounted() {
        val buffer = OpusFrameBuffer()
        buffer.add(ByteArray(MAX_FRAME_BYTES + 1) { it.toByte() })
        assertEquals(1, buffer.droppedOversized)
        assertEquals(0, buffer.frameCount)

        // Нормальный кадр после отброса всё ещё принимается.
        buffer.add(byteArrayOf(9))
        assertEquals(1, buffer.frameCount)
        assertEquals(1, unpackFrames(buffer.drain()!!).size)
    }

    @Test
    fun shouldFlushByTime() {
        val buffer = OpusFrameBuffer()
        assertFalse(buffer.shouldFlush(nowMs = 0, flushMs = 5_000, maxFrames = 100))

        buffer.add(byteArrayOf(1))
        val now = System.currentTimeMillis()
        // Сразу после добавления сливать рано (запас 50 мс на погрешность часов).
        assertFalse(buffer.shouldFlush(nowMs = now, flushMs = 5_000, maxFrames = 100))
        assertTrue(buffer.shouldFlush(nowMs = now + 5_050, flushMs = 5_000, maxFrames = 100))
    }

    @Test
    fun shouldFlushByFrameCount() {
        val buffer = OpusFrameBuffer()
        for (i in 0 until 100) buffer.add(byteArrayOf(i.toByte()))
        // Время не подошло, но кадров набралось — пора.
        assertTrue(
            buffer.shouldFlush(
                nowMs = System.currentTimeMillis(),
                flushMs = Long.MAX_VALUE / 2,
                maxFrames = 100,
            )
        )
    }
}
