package ru.uzel.bridge.core

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder
import java.io.File

class NotesStoreTest {

    @get:Rule
    val tmp = TemporaryFolder()

    private var now = 1_000L
    private fun store() = NotesStore(File(tmp.root, "notes.json")) { now }

    @Test
    fun `добавление, порядок и переживание перезапуска`() {
        val s = store()
        val a = s.add("  первая  ")
        now = 2_000
        val b = s.add("вторая")
        now = 3_000
        s.update(a.id) { it.copy(pinned = true) }
        // Новый экземпляр — как после убийства процесса.
        val all = store().all()
        assertEquals(listOf(a.id, b.id), all.map { it.id })
        assertEquals("первая", all[0].text)
        assertTrue(all[0].pinned)
        assertEquals(1_000L, all[0].createdAt)
        assertEquals(3_000L, all[0].updatedAt)
    }

    @Test
    fun `две заметки в одну миллисекунду — разные id`() {
        val s = store()
        val a = s.add("раз")
        val b = s.add("два", moment = true)
        assertNotEquals(a.id, b.id)
        assertTrue(store().all().first { it.id == b.id }.moment)
    }

    @Test
    fun `удаление и возврат`() {
        val s = store()
        val a = s.add("x")
        val gone = s.delete(a.id)!!
        assertTrue(s.all().isEmpty())
        s.restore(gone)
        assertEquals(listOf(a), s.all())
        assertNull(s.delete(12345))
    }

    @Test
    fun `битый файл откладывается рядом, а не теряется`() {
        val f = File(tmp.root, "notes.json")
        f.writeText("{обрыв", Charsets.UTF_8)
        val s = store()
        assertTrue(s.all().isEmpty())
        assertFalse(f.exists())
        assertEquals(1, tmp.root.listFiles()!!.count { it.name.startsWith("notes.json.broken-") })
        s.add("новая")
        assertEquals(1, s.all().size)
    }

    @Test
    fun `метка находит свой разговор`() {
        fun card(id: Long, from: Double, to: Double) = ConversationCard(
            id, from, to, "", "", "", "closed", null, emptyList(), emptyList(), 0, 0, 0,
        )
        val cards = listOf(card(1, 1000.0, 1600.0), card(2, 2000.0, 2300.0))
        assertEquals(1L, conversationAt(cards, 1_200_000)?.id)
        // Сразу после конца разговора — всё ещё он.
        assertEquals(2L, conversationAt(cards, 2_400_000)?.id)
        // Далеко от всех — ничего.
        assertNull(conversationAt(cards, 9_000_000))
    }

    @Test
    fun `снимок состояния — туда и обратно, свежесть`() {
        val f = File(tmp.root, "state.json")
        val snap = BridgeSnapshot(running = true, connected = true, batteryPercent = null,
            codec = "opus", packets = 990, lost = 10, sent = 5, writtenAt = 10_000)
        snap.writeTo(f)
        val back = BridgeSnapshot.readFrom(f)
        assertEquals(snap, back)
        assertEquals(0.01, back.lossRatio, 1e-9)
        assertTrue(back.isFresh(now = 20_000))
        assertFalse(back.isFresh(now = 40_000))
        assertFalse(BridgeSnapshot.readFrom(File(tmp.root, "нет.json")).running)
    }
}
