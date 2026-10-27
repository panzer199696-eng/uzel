package ru.uzel.bridge.core

import org.json.JSONArray
import org.json.JSONObject
import java.io.File

/**
 * Заметка на телефоне. [moment] — «метка момента»: поставлена кнопкой
 * (в приложении, в уведомлении, плиткой в шторке) и привязана ко времени,
 * чтобы потом найти разговор, который кулон записал в эту минуту.
 */
data class Note(
    val id: Long,
    val text: String,
    val createdAt: Long,
    val updatedAt: Long,
    val pinned: Boolean = false,
    val done: Boolean = false,
    val moment: Boolean = false,
)

/**
 * Заметки в одном JSON-файле. Пишется атомарно (временный файл + rename):
 * обрыв посреди записи оставляет прошлую версию, а не пустой файл.
 * Методы синхронизированы — заметку могут добавить одновременно экран и
 * кнопка в уведомлении. Перечитывает файл на каждом вызове: писатели
 * живут в разных компонентах, общего кэша у них нет.
 */
class NotesStore(private val file: File, private val clock: () -> Long = System::currentTimeMillis) {

    @Synchronized
    fun all(): List<Note> = load().sortedWith(ORDER)

    @Synchronized
    fun add(text: String, moment: Boolean = false, at: Long? = null): Note {
        val notes = load()
        val now = at ?: clock()
        // id — время в мс; две заметки в одну миллисекунду не должны совпасть.
        var id = now
        while (notes.any { it.id == id }) id++
        val note = Note(id = id, text = text.trim(), createdAt = now, updatedAt = now, moment = moment)
        save(notes + note)
        return note
    }

    @Synchronized
    fun update(id: Long, change: (Note) -> Note): Note? {
        val notes = load()
        val old = notes.firstOrNull { it.id == id } ?: return null
        val new = change(old).copy(id = old.id, createdAt = old.createdAt, updatedAt = clock())
        save(notes.map { if (it.id == id) new else it })
        return new
    }

    @Synchronized
    fun delete(id: Long): Note? {
        val notes = load()
        val gone = notes.firstOrNull { it.id == id } ?: return null
        save(notes.filterNot { it.id == id })
        return gone
    }

    /** Вернуть удалённую заметку (кнопка «Отменить»). */
    @Synchronized
    fun restore(note: Note) {
        val notes = load().filterNot { it.id == note.id }
        save(notes + note)
    }

    private fun load(): List<Note> {
        if (!file.exists()) return emptyList()
        return try {
            val arr = JSONArray(file.readText(Charsets.UTF_8))
            (0 until arr.length()).mapNotNull { arr.optJSONObject(it)?.let(::fromJson) }
        } catch (e: Exception) {
            // Битый файл не теряем молча: откладываем рядом и начинаем с чистого.
            file.renameTo(File(file.parentFile, file.name + ".broken-" + clock()))
            emptyList()
        }
    }

    private fun save(notes: List<Note>) {
        file.parentFile?.mkdirs()
        val arr = JSONArray()
        notes.forEach { arr.put(toJson(it)) }
        val tmp = File(file.parentFile, file.name + ".tmp")
        tmp.writeText(arr.toString(), Charsets.UTF_8)
        if (!tmp.renameTo(file)) {
            file.delete()
            check(tmp.renameTo(file)) { "не удалось сохранить заметки" }
        }
    }

    companion object {
        /** Закреплённые сверху, дальше — сначала новые. */
        val ORDER: Comparator<Note> =
            compareByDescending<Note> { it.pinned }.thenByDescending { it.createdAt }

        fun toJson(n: Note): JSONObject = JSONObject()
            .put("id", n.id)
            .put("text", n.text)
            .put("created_at", n.createdAt)
            .put("updated_at", n.updatedAt)
            .put("pinned", n.pinned)
            .put("done", n.done)
            .put("moment", n.moment)

        fun fromJson(o: JSONObject) = Note(
            id = o.getLong("id"),
            text = o.optString("text"),
            createdAt = o.optLong("created_at"),
            updatedAt = o.optLong("updated_at", o.optLong("created_at")),
            pinned = o.optBoolean("pinned"),
            done = o.optBoolean("done"),
            moment = o.optBoolean("moment"),
        )
    }
}

/**
 * Разговор, к которому относится момент [atMs]: тот, что шёл в это время,
 * иначе ближайший в пределах [slackS] секунд (метку часто ставят сразу
 * после фразы, а разговор уже закрыт). `null` — кулон тогда не писал.
 */
fun conversationAt(cards: List<ConversationCard>, atMs: Long, slackS: Double = 300.0): ConversationCard? {
    val t = atMs / 1000.0
    cards.firstOrNull { t >= it.startedAt && t <= it.endedAt }?.let { return it }
    return cards
        .map { it to minOf(kotlin.math.abs(t - it.startedAt), kotlin.math.abs(t - it.endedAt)) }
        .filter { it.second <= slackS }
        .minByOrNull { it.second }
        ?.first
}
