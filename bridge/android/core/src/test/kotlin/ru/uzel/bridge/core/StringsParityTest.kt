package ru.uzel.bridge.core

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import org.w3c.dom.Element
import java.io.File
import javax.xml.parsers.DocumentBuilderFactory

/**
 * Строки приложения: values/ (английский, умолчание) и каждый перевод values-<код>/
 * обязаны иметь одинаковые имена и одинаковые аргументы формата (%1$s, %2$d…).
 * Иначе на телефоне с другим языком всплывёт английская строка или упадёт
 * String.format. Тест живёт в :core, потому что гоняется без Android SDK.
 */
class StringsParityTest {

    private val res = File("../app/src/main/res")

    private data class Entry(val kind: String, val args: Set<String>)

    private fun read(file: File): Map<String, Entry> {
        val doc = DocumentBuilderFactory.newInstance().newDocumentBuilder().parse(file)
        val out = HashMap<String, Entry>()
        val strings = doc.getElementsByTagName("string")
        for (i in 0 until strings.length) {
            val e = strings.item(i) as Element
            if (e.getAttribute("translatable") == "false") continue
            out[e.getAttribute("name")] = Entry("string", args(e.textContent))
        }
        val plurals = doc.getElementsByTagName("plurals")
        for (i in 0 until plurals.length) {
            val e = plurals.item(i) as Element
            val items = e.getElementsByTagName("item")
            val all = (0 until items.length).flatMap { args(items.item(it).textContent) }.toSet()
            out[e.getAttribute("name")] = Entry("plurals", all)
        }
        return out
    }

    private fun args(text: String): Set<String> =
        Regex("%(\\d+\\$)?[sdf]").findAll(text).map { it.value }.toSet()

    @Test
    fun `переводы совпадают с английским по именам и аргументам`() {
        val base = read(File(res, "values/strings.xml"))
        assertTrue("строк слишком мало — не тот файл?", base.size > 100)
        val translations = res.listFiles { f -> f.isDirectory && f.name.startsWith("values-") }!!
            .map { File(it, "strings.xml") }
            .filter { it.exists() }
        assertTrue("нет ни одного перевода", translations.isNotEmpty())
        for (file in translations) {
            val other = read(file)
            assertEquals("${file.parentFile.name}: имена", base.keys.sorted(), other.keys.sorted())
            for ((name, entry) in base) {
                assertEquals("${file.parentFile.name}: $name", entry, other[name])
            }
        }
    }
}
