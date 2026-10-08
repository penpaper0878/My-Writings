package io.github.penpaper0878.pdf2md.core

import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.boolean
import kotlinx.serialization.json.double
import kotlinx.serialization.json.int
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import java.io.File
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertTrue

/**
 * The desktop tool's own page data and output (exported by
 * android/tools/export_parity.py) must give the same Markdown here.
 */
class ParityTest {

    private fun box(a: JsonArray) = Box(a[0].jsonPrimitive.double, a[1].jsonPrimitive.double, a[2].jsonPrimitive.double, a[3].jsonPrimitive.double)

    private fun load(json: String): Pair<List<Pair<PageData, List<Digital.Placed>>>, String> {
        val root = Json.parseToJsonElement(json).jsonObject
        val pages = root["pages"]!!.jsonArray.map { pe ->
            val p = pe.jsonObject
            val blocks = p["blocks"]!!.jsonArray.map { be ->
                be.jsonArray.map { le ->
                    val l = le.jsonObject
                    val spans = l["spans"]!!.jsonArray.map { se ->
                        val s = se.jsonObject
                        Span(
                            text = s["text"]!!.jsonPrimitive.content,
                            bbox = box(s["bbox"]!!.jsonArray),
                            size = s["size"]!!.jsonPrimitive.double,
                            bold = s["bold"]!!.jsonPrimitive.boolean,
                            italic = s["italic"]!!.jsonPrimitive.boolean,
                            mono = s["mono"]!!.jsonPrimitive.boolean,
                            sup = s["sup"]!!.jsonPrimitive.boolean,
                            strike = s["strike"]!!.jsonPrimitive.boolean,
                            link = s["link"]?.jsonPrimitive?.takeIf { it.isString }?.content,
                        )
                    }.toMutableList()
                    Line(spans, box(l["bbox"]!!.jsonArray))
                }
            }
            val tables = (p["tables"] as JsonArray).map { te ->
                val t = te.jsonObject
                Digital.Placed(box(t["bbox"]!!.jsonArray), t["markdown"]!!.jsonPrimitive.content)
            }
            PageData(p["number"]!!.jsonPrimitive.int, p["width"]!!.jsonPrimitive.double, p["height"]!!.jsonPrimitive.double, blocks) to tables
        }
        return pages to root["markdown"]!!.jsonPrimitive.content
    }

    private fun convert(json: String): Pair<String, String> {
        val (pages, expected) = load(json)
        val typed = Pipeline.typedPages(
            pages.map { it.first },
            tables = pages.associate { it.first.number to it.second },
        )
        val result = Pipeline.document(
            pages.map { Pipeline.Page(it.first.number, typed.markdown.getValue(it.first.number), "text") },
            typed.style.vocab,
        )
        return result to expected
    }

    private fun fixtures(): List<File> {
        val dirs = listOfNotNull(
            javaClass.getResource("/parity")?.let { File(it.toURI()) },
            System.getenv("PDF2MD_EXTRA_PARITY")?.let { File(it) },
        )
        return dirs.flatMap { d -> d.listFiles { f -> f.name.endsWith(".json") }.orEmpty().sortedBy { it.name } }
    }

    @Test
    fun everyFixtureMatchesTheDesktopTool() {
        val files = fixtures()
        assertTrue(files.size >= 4, "parity fixtures missing")
        for (f in files) {
            val (actual, expected) = convert(f.readText())
            if (actual != expected) {
                val a = actual.lines()
                val e = expected.lines()
                val i = (0 until maxOf(a.size, e.size)).first { a.getOrNull(it) != e.getOrNull(it) }
                assertEquals(e.getOrNull(i), a.getOrNull(i), "${f.name}: first difference at line ${i + 1}")
            }
            assertEquals(expected, actual, f.name)
        }
    }
}
