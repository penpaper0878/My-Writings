package io.github.penpaper0878.pdf2md.convert

import io.github.penpaper0878.pdf2md.core.OcrResult
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import kotlinx.serialization.json.put
import java.io.File
import java.security.MessageDigest

/**
 * Transcriptions already made, keyed on the exact page pixels and reader
 * settings: converting the same notes again, or resuming after an
 * interruption, costs nothing.
 */
class OcrCache(private val dir: File) {
    init {
        dir.mkdirs()
        // Keep the cache from growing forever: forget pages untouched for 60 days.
        val cutoff = System.currentTimeMillis() - 60L * 24 * 3600 * 1000
        dir.listFiles()?.filter { it.lastModified() < cutoff }?.forEach { it.delete() }
    }

    fun key(signature: String, width: Int, height: Int, gray: ByteArray): String {
        val md = MessageDigest.getInstance("SHA-256")
        md.update(signature.toByteArray())
        md.update("$width x $height".toByteArray())
        md.update(gray)
        return md.digest().joinToString("") { "%02x".format(it) }
    }

    fun get(key: String): OcrResult? = runCatching {
        val o = Json.parseToJsonElement(File(dir, "$key.json").readText()).jsonObject
        OcrResult(o["markdown"]!!.jsonPrimitive.content, o["warnings"]!!.jsonArray.map { it.jsonPrimitive.content })
    }.getOrNull()

    fun put(key: String, result: OcrResult) {
        runCatching {
            val tmp = File(dir, "$key.tmp")
            tmp.writeText(
                buildJsonObject {
                    put("markdown", result.markdown)
                    put("warnings", JsonArray(result.warnings.map { JsonPrimitive(it) }))
                }.toString(),
            )
            tmp.renameTo(File(dir, "$key.json"))
        }
    }
}
