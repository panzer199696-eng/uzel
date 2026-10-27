package ru.uzel.bridge.core

import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import java.nio.file.Files

/** Тесты дискового спула (ТЗ P8, пункт 4). */
class DiskSpoolTest {

    private lateinit var dir: java.io.File

    @org.junit.Before
    fun setUp() {
        dir = Files.createTempDirectory("uzel-spool-test").toFile()
    }

    @org.junit.After
    fun tearDown() {
        dir.deleteRecursively()
    }

    @Test
    fun pendingReturnsChunksInOrder() {
        val spool = DiskSpool(dir)
        val first = ByteArray(10) { 1 }
        val second = ByteArray(10) { 2 }
        val third = ByteArray(10) { 3 }
        spool.add(first)
        spool.add(second)
        spool.add(third)

        val pending = spool.pending(10)
        assertEquals(3, pending.size)
        assertArrayEquals(first, pending[0].read())
        assertArrayEquals(second, pending[1].read())
        assertArrayEquals(third, pending[2].read())
    }

    @Test
    fun dropRemovesBothFiles() {
        val spool = DiskSpool(dir)
        val chunk = spool.add(ByteArray(8))
        val json = java.io.File(dir, chunk.path.nameWithoutExtension + ".json")
        assertTrue(chunk.path.exists())
        assertTrue(json.exists())

        spool.drop(chunk)
        assertFalse(chunk.path.exists())
        assertFalse(json.exists())
        assertEquals(0, spool.count())
    }

    @Test
    fun countAndTotalBytes() {
        val spool = DiskSpool(dir)
        spool.add(ByteArray(10))
        spool.add(ByteArray(15))
        assertEquals(2, spool.count())
        assertEquals(25L, spool.totalBytes())
    }

    @Test
    fun sizeLimitDropsOldestKeepsNew() {
        val spool = DiskSpool(dir, maxBytes = 1_000L)
        val oldest = ByteArray(500) { 1 }
        val middle = ByteArray(500) { 2 }
        val newest = ByteArray(500) { 3 }
        spool.add(oldest)   // 500 <= 1000 — влезает
        spool.add(middle)   // 1000 <= 1000 — влезает
        spool.add(newest)   // 1500 > 1000 — самый старый удаляется

        assertEquals(1, spool.droppedOld)
        assertEquals(2, spool.count())
        assertEquals(1_000L, spool.totalBytes())
        // Остались средний и новый, старого нет.
        val pending = spool.pending(10)
        assertArrayEquals(middle, pending[0].read())
        assertArrayEquals(newest, pending[1].read())
    }

    @Test
    fun orphanOpusWithoutJsonNotInPending() {
        val spool = DiskSpool(dir)
        spool.add(ByteArray(6))
        // Метаданные пропали — фрагмент битый, в pending не попадает.
        dir.listFiles { f -> f.name.endsWith(".json") }!!.forEach { it.delete() }

        assertTrue(spool.pending(10).isEmpty())

        // И наоборот: посторонний .opus без .json тоже невидим.
        java.io.File(dir, "9999999999999999999_000001.opus").writeBytes(ByteArray(4))
        assertTrue(spool.pending(10).isEmpty())
    }

    @Test
    fun deviceTimeMsSurvivesRoundTrip() {
        val spool = DiskSpool(dir)
        spool.add(ByteArray(5), SpoolMeta(deviceTimeMs = 123_456L))
        spool.add(ByteArray(5)) // без метки времени

        val pending = spool.pending(10)
        assertEquals(123_456L, pending[0].deviceTimeMs)
        assertEquals(null, pending[1].deviceTimeMs)
    }

    @Test
    fun seqContinuesAcrossInstances() {
        val first = DiskSpool(dir)
        first.add(ByteArray(4))
        val second = DiskSpool(dir)
        // Новый экземпляр не должен затирать старые имена.
        second.add(ByteArray(4))
        assertEquals(2, second.count())
    }
}
