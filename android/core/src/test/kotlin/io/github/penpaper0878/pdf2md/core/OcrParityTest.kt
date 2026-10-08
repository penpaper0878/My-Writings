package io.github.penpaper0878.pdf2md.core

import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonNull
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.boolean
import kotlinx.serialization.json.double
import kotlinx.serialization.json.doubleOrNull
import kotlinx.serialization.json.int
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import java.io.File
import javax.imageio.ImageIO
import kotlin.test.Test
import kotlin.test.assertEquals

/** OCR-side behaviour must match the desktop tool (fixtures from export_ocr_parity.py). */
class OcrParityTest {
    private val dir = File(javaClass.getResource("/parity-ocr")!!.toURI())
    private val fx: JsonObject = Json.parseToJsonElement(File(dir, "ocr.json").readText()).jsonObject

    private fun str(o: JsonObject, k: String): String? = o[k]?.takeIf { it !is JsonNull }?.jsonPrimitive?.content

    @Test
    fun prompts() {
        for (e in fx["prompts"]!!.jsonArray) {
            val o = e.jsonObject
            val actual = Prompt.build(o["part"]!!.jsonPrimitive.int, o["parts"]!!.jsonPrimitive.int, str(o, "lang"), str(o, "hint"))
            assertEquals(str(o, "prompt"), actual)
        }
    }

    @Test
    fun modelOutputCleanup() {
        for (e in fx["cleanup"]!!.jsonArray) {
            val o = e.jsonObject
            val input = str(o, "input")!!
            val clean = OcrCleanup.cleanModelOutput(input)
            assertEquals(str(o, "clean"), clean, "clean: $input")
            assertEquals(o["degenerate"]!!.jsonPrimitive.boolean, OcrCleanup.looksDegenerate(clean), "degenerate: $input")
            assertEquals(str(o, "collapsed"), OcrCleanup.collapseRepetition(clean), "collapse: $input")
        }
    }

    @Test
    fun ocrLayoutFromRealTesseractOutput() {
        val layouts = fx["layouts"]!!.jsonArray
        for (e in layouts) {
            val o = e.jsonObject
            val words = o["words"]!!.jsonArray.map { w ->
                val x = w.jsonObject
                OcrWord(
                    str(x, "text")!!, x["left"]!!.jsonPrimitive.int, x["top"]!!.jsonPrimitive.int,
                    x["width"]!!.jsonPrimitive.int, x["height"]!!.jsonPrimitive.int, x["conf"]!!.jsonPrimitive.double,
                    x["block"]!!.jsonPrimitive.int, x["par"]!!.jsonPrimitive.int, x["line"]!!.jsonPrimitive.int,
                )
            }
            val (md, conf) = OcrLayout.toMarkdown(words)
            assertEquals(str(o, "markdown"), md)
            val expectedConf = o["confidence"]!!.jsonPrimitive.doubleOrNull
            if (expectedConf == null) assertEquals(null, conf) else assertEquals(expectedConf, conf!!, 1e-9)
        }
    }

    @Test
    fun repeatedEdgesOfOcrPages() {
        for (e in fx["edges"]!!.jsonArray) {
            val o = e.jsonObject
            val pages = o["pages"]!!.jsonArray.map {
                val p = it.jsonObject
                EdgeStrip.OcrPage(str(p, "markdown")!!, p["printed"]!!.jsonPrimitive.boolean)
            }
            EdgeStrip.strip(pages)
            assertEquals(o["after"]!!.jsonArray.map { it.jsonPrimitive.content }, pages.map { it.markdown })
        }
    }

    @Test
    fun stripCutsAndBlankPages() {
        for (e in fx["images"]!!.jsonArray) {
            val o = e.jsonObject
            val img = ImageIO.read(File(dir, str(o, "file")!!))
            val raster = img.raster
            val gray = ByteArray(img.width * img.height)
            val px = IntArray(1)
            for (y in 0 until img.height) for (x in 0 until img.width) {
                raster.getPixel(x, y, px)
                gray[y * img.width + x] = px[0].toByte()
            }
            for ((parts, cuts) in o["cuts"]!!.jsonObject) {
                val expected = cuts.jsonArray.map { it.jsonPrimitive.int }
                assertEquals(expected, PageImage.findCuts(gray, img.width, img.height, parts.toInt()), "${str(o, "file")} parts=$parts")
            }
            assertEquals(o["blank"]!!.jsonPrimitive.boolean, PageImage.isBlank(gray), "${str(o, "file")} blank")
        }
    }

    @Test
    fun loopDetectionIsFastOnLongText() {
        val rnd = java.util.Random(0)
        val chars = "abcdefghijklmnopqrstuvwxyz \n"
        val text = String(CharArray(6000) { chars[rnd.nextInt(chars.length)] })
        val t0 = System.nanoTime()
        OcrCleanup.looksDegenerate(text)
        val ms = (System.nanoTime() - t0) / 1e6
        kotlin.test.assertTrue(ms < 2000, "took $ms ms")
    }
}
