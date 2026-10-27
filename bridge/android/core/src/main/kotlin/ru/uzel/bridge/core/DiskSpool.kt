package ru.uzel.bridge.core

import java.io.File
import java.nio.file.Files
import java.util.Locale

/** Метаданные фрагмента в спуле. */
data class SpoolMeta(
    /** Время по часам телефона в момент накопления фрагмента. */
    val deviceTimeMs: Long? = null,
)

/** Отложенный фрагмент: тело в `.opus`, метаданные в `.json` рядом. */
class SpooledChunk(
    /** Файл с телом запроса в формате `opus-frames/1`. */
    val path: File,
    /** Астрономическое время накопления в наносекундах. */
    val spooledNs: Long,
    /** Порядковый номер внутри спула. */
    val seq: Int,
    val nBytes: Int,
    val deviceTimeMs: Long?,
) {
    fun read(): ByteArray = path.readBytes()
}

/**
 * Очередь неотправленного на диске — аналог `bridge/uzel_bridge/spool.py`.
 *
 * Телефон без связи сутками не должен забивать память насмерть: при
 * превышении `maxBytes` удаляются самые старые фрагменты, счётчик
 * удалений копится в [droppedOld].
 */
class DiskSpool(
    rootDir: File,
    private val maxBytes: Long = DEFAULT_MAX_BYTES,
) {
    val root: File = rootDir.apply { mkdirs() }

    /** Сколько старых фрагментов удалено из-за лимита размера. */
    var droppedOld: Int = 0
        private set

    private var seq: Int = scanMaxSeq() + 1

    companion object {
        const val DEFAULT_MAX_BYTES: Long = 256L * 1024 * 1024

        /** Числа в наших метаданных — только эти ключи, все целые. */
        private val DEVICE_TIME = Regex("\"device_time_ms\"\\s*:\\s*(-?\\d+)")
    }

    /** Кладёт фрагмент в спул атомарной записью пары файлов. */
    fun add(payload: ByteArray, meta: SpoolMeta = SpoolMeta()): SpooledChunk {
        require(payload.isNotEmpty()) { "пустой фрагмент" }
        val ns = System.currentTimeMillis() * 1_000_000L
        val mySeq = seq++
        // Оба поля с фиксированной шириной: лексикографический порядок
        // имён совпадает с хронологическим.
        val base = String.format(Locale.ROOT, "%019d_%06d", ns, mySeq)
        val opus = File(root, "$base.opus")
        val json = File(root, "$base.json")

        atomicWrite(opus, payload)
        val jsonText = buildString {
            append("{\"spooled_ns\":").append(ns)
            append(",\"seq\":").append(mySeq)
            append(",\"n_bytes\":").append(payload.size)
            append(",\"device_time_ms\":").append(meta.deviceTimeMs ?: "null")
            append("}")
        }
        atomicWrite(json, jsonText.toByteArray(Charsets.UTF_8))

        val chunk = SpooledChunk(opus, ns, mySeq, payload.size, meta.deviceTimeMs)
        enforceLimit()
        return chunk
    }

    /** Неотправленные фрагменты по возрастанию времени накопления. */
    fun pending(limit: Int = Int.MAX_VALUE): List<SpooledChunk> {
        val jsons = root.listFiles { f -> f.isFile && f.name.endsWith(".json") }
            ?: return emptyList()
        val result = ArrayList<SpooledChunk>()
        for (jsonFile in jsons.sortedBy { it.name }) {
            if (result.size >= limit) break
            val opus = File(root, jsonFile.name.removeSuffix(".json") + ".opus")
            // Тело пропало, а метаданные остались — фрагмент битый, пропускаем.
            if (!opus.exists()) continue
            val (ns, mySeq) = parseName(jsonFile.name)
            result.add(
                SpooledChunk(
                    path = opus,
                    spooledNs = ns,
                    seq = mySeq,
                    nBytes = opus.length().toInt(),
                    deviceTimeMs = readDeviceTimeMs(jsonFile),
                )
            )
        }
        return result
    }

    /** Удаляет фрагмент из спула (оба файла). */
    fun drop(chunk: SpooledChunk) {
        chunk.path.delete()
        File(root, chunk.path.nameWithoutExtension + ".json").delete()
    }

    /** Число фрагментов в спуле. */
    fun count(): Int = countFiles(".json")

    /** Суммарный размер тел фрагментов в байтах. */
    fun totalBytes(): Long {
        val files = root.listFiles { f -> f.isFile && f.name.endsWith(".opus") }
            ?: return 0L
        return files.sumOf { it.length() }
    }

    private fun countFiles(ext: String): Int {
        val files = root.listFiles { f -> f.isFile && f.name.endsWith(ext) } ?: return 0
        return files.size
    }

    /** Удаляет самые старые фрагменты, пока спул не влезет в лимит. */
    private fun enforceLimit() {
        while (totalBytes() > maxBytes) {
            val oldest = pending(1).firstOrNull() ?: break
            drop(oldest)
            droppedOld++
        }
    }

    /** Атомарная запись: во временный файл, затем переименование. */
    private fun atomicWrite(target: File, bytes: ByteArray) {
        val tmp = File(target.parentFile, target.name + ".tmp")
        tmp.writeBytes(bytes)
        if (target.exists()) target.delete()
        if (!tmp.renameTo(target)) {
            // Переименование не вышло (например, файл залокан антивирусом) —
            // пишем напрямую: лучше неатомарно, чем потерять фрагмент.
            target.writeBytes(bytes)
            tmp.delete()
        }
    }

    private fun parseName(jsonName: String): Pair<Long, Int> {
        val parts = jsonName.removeSuffix(".json").split("_")
        val ns = parts.getOrNull(0)?.toLongOrNull() ?: 0L
        val mySeq = parts.getOrNull(1)?.toIntOrNull() ?: 0
        return ns to mySeq
    }

    private fun readDeviceTimeMs(jsonFile: File): Long? {
        val text = try {
            jsonFile.readText(Charsets.UTF_8)
        } catch (e: Exception) {
            return null
        }
        return DEVICE_TIME.find(text)?.groupValues?.get(1)?.toLongOrNull()
    }

    /** Порядковый номер продолжается после перезапуска, чтобы имена не collide-ились. */
    private fun scanMaxSeq(): Int {
        val jsons = root.listFiles { f -> f.isFile && f.name.endsWith(".json") }
            ?: return -1
        var best = -1
        for (f in jsons) {
            val parts = f.name.removeSuffix(".json").split("_")
            parts.getOrNull(1)?.toIntOrNull()?.let { if (it > best) best = it }
        }
        return best
    }
}
