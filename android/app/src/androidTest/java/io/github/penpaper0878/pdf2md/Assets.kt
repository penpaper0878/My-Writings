package io.github.penpaper0878.pdf2md

import androidx.test.platform.app.InstrumentationRegistry
import io.github.penpaper0878.pdf2md.core.Box
import io.github.penpaper0878.pdf2md.core.Digital
import io.github.penpaper0878.pdf2md.core.Line
import io.github.penpaper0878.pdf2md.core.PageData
import io.github.penpaper0878.pdf2md.core.Span
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonNull
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.boolean
import kotlinx.serialization.json.double
import kotlinx.serialization.json.int
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import java.io.File

/** Test files packaged with the test APK. */
object Assets {
    private val testContext get() = InstrumentationRegistry.getInstrumentation().context
    val target get() = InstrumentationRegistry.getInstrumentation().targetContext

    fun file(name: String): File {
        val out = File(target.cacheDir, "test-assets/" + name.replace('/', '_'))
        out.parentFile!!.mkdirs()
        testContext.assets.open(name).use { input -> out.outputStream().use { input.copyTo(it) } }
        return out
    }

    fun text(name: String): String = testContext.assets.open(name).use { it.readBytes().toString(Charsets.UTF_8) }

    class Fixture(val pages: List<PageData>, val tables: Map<Int, List<Digital.Placed>>, val markdown: String)

    private fun box(a: JsonArray) = Box(a[0].jsonPrimitive.double, a[1].jsonPrimitive.double, a[2].jsonPrimitive.double, a[3].jsonPrimitive.double)

    /** The desktop tool's page data and Markdown, exported by android/tools/export_parity.py. */
    fun fixture(name: String): Fixture {
        val root = Json.parseToJsonElement(text("parity/$name.json")).jsonObject
        val tables = LinkedHashMap<Int, List<Digital.Placed>>()
        val pages = root["pages"]!!.jsonArray.map { pe ->
            val p = pe.jsonObject
            val number = p["number"]!!.jsonPrimitive.int
            tables[number] = (p["tables"] as JsonArray).map {
                val t = it.jsonObject
                Digital.Placed(box(t["bbox"]!!.jsonArray), t["markdown"]!!.jsonPrimitive.content)
            }
            PageData(
                number, p["width"]!!.jsonPrimitive.double, p["height"]!!.jsonPrimitive.double,
                p["blocks"]!!.jsonArray.map { be ->
                    be.jsonArray.map { le ->
                        val l = le.jsonObject
                        Line(
                            l["spans"]!!.jsonArray.map { se ->
                                val s = se.jsonObject
                                Span(
                                    s["text"]!!.jsonPrimitive.content, box(s["bbox"]!!.jsonArray), s["size"]!!.jsonPrimitive.double,
                                    s["bold"]!!.jsonPrimitive.boolean, s["italic"]!!.jsonPrimitive.boolean,
                                    s["mono"]!!.jsonPrimitive.boolean, s["sup"]!!.jsonPrimitive.boolean,
                                    s["strike"]!!.jsonPrimitive.boolean,
                                    s["link"]?.takeIf { it !is JsonNull }?.jsonPrimitive?.content,
                                )
                            }.toMutableList(),
                            box(l["bbox"]!!.jsonArray),
                        )
                    }
                },
            )
        }
        return Fixture(pages, tables, root["markdown"]!!.jsonPrimitive.content)
    }
}
