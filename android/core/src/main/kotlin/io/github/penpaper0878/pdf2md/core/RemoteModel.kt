package io.github.penpaper0878.pdf2md.core

import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonNull
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.buildJsonArray
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import kotlinx.serialization.json.put
import kotlinx.serialization.json.putJsonArray
import kotlinx.serialization.json.putJsonObject
import java.io.IOException
import java.net.HttpURLConnection
import java.net.Inet4Address
import java.net.Inet6Address
import java.net.InetAddress
import java.net.Proxy
import java.net.URI
import java.net.URL
import java.util.Base64

/**
 * A vision model served by Ollama or an OpenAI-compatible server (llama.cpp,
 * LM Studio, vLLM) on this device or the local network: the phone's
 * "my computer" mode. Same protocol and settings as the desktop tool.
 */
class RemoteModel(
    var model: String,
    val api: String = "ollama",
    baseUrl: String? = null,
    private val apiKey: String? = null,
    private val timeoutMs: Int = 900_000,
    private val allowRemote: Boolean = false,
    private val maxSide: Int = 2048,
    private val maxTokens: Int = 6144,
    private val context: Int = 16384,
) : VisionModel {
    val baseUrl: String = normalizeBase(baseUrl ?: if (api == "ollama") "http://127.0.0.1:11434" else "http://127.0.0.1:8080", api)
    private var thinking = false

    /** Advice about the setup, worth showing once. */
    val notes = mutableListOf<String>()

    override val signature: String get() = listOf(api, model, maxSide).joinToString("|")
    override val description: String get() = "$model at $baseUrl"

    companion object {
        fun normalizeBase(url: String, api: String): String {
            var u = url.trim().trimEnd('/')
            if (!u.contains("://")) u = "http://$u"
            if (api == "openai" && u.endsWith("/v1")) u = u.dropLast(3)
            val parsed = URI(u)
            if (parsed.port == -1 && api == "ollama") u = "${parsed.scheme}://${parsed.host}:11434${parsed.path ?: ""}"
            return u
        }

        /** True if the host is this device or on a private network. */
        fun isLocalUrl(url: String): Boolean {
            val host = (try { URI(url).host } catch (e: Exception) { null })?.trim('[', ']')?.lowercase() ?: return false
            if (host.isEmpty()) return false
            if (host == "localhost" || host.endsWith(".localhost") || host.endsWith(".local") || host.endsWith(".lan")) return true
            val addrs = try { InetAddress.getAllByName(host).toList() } catch (e: Exception) { return false }
            return addrs.isNotEmpty() && addrs.all { isPrivate(it) }
        }

        private fun isPrivate(a: InetAddress): Boolean {
            if (a.isLoopbackAddress || a.isLinkLocalAddress || a.isSiteLocalAddress) return true
            if (a is Inet6Address) return (a.address[0].toInt() and 0xfe) == 0xfc // fc00::/7
            if (a is Inet4Address) {
                val b = a.address.map { it.toInt() and 0xff }
                return b[0] == 10 || (b[0] == 172 && b[1] in 16..31) || (b[0] == 192 && b[1] == 168)
            }
            return false
        }
    }

    private val local: Boolean by lazy { isLocalUrl(this.baseUrl) }

    private fun guard() {
        if (!allowRemote && !local) {
            throw EngineUnavailable(
                "${this.baseUrl} is not on this device or your local network. Pages stay private, so only " +
                    "a computer on your own Wi-Fi can be used.",
            )
        }
    }

    private fun request(method: String, path: String, payload: JsonElement? = null, timeout: Int = timeoutMs): JsonObject {
        val url = URL(this.baseUrl + path)
        // A server on this device or the home network must not go through a proxy.
        val conn = (if (local) url.openConnection(Proxy.NO_PROXY) else url.openConnection()) as HttpURLConnection
        conn.requestMethod = method
        conn.connectTimeout = minOf(timeout, 10_000)
        conn.readTimeout = timeout
        conn.setRequestProperty("Content-Type", "application/json")
        if (apiKey != null && api == "openai") conn.setRequestProperty("Authorization", "Bearer $apiKey")
        try {
            if (payload != null) {
                conn.doOutput = true
                conn.outputStream.use { it.write(payload.toString().toByteArray(Charsets.UTF_8)) }
            }
            val code = conn.responseCode
            val stream = if (code in 200..299) conn.inputStream else conn.errorStream
            val body = stream?.use { it.readBytes().toString(Charsets.UTF_8) } ?: ""
            if (code !in 200..299) throw HttpStatus(code, body.take(300))
            return try {
                Json.parseToJsonElement(body).jsonObject
            } catch (e: Exception) {
                throw EngineError("the model server sent an unreadable answer: ${body.take(120)}", e)
            }
        } finally {
            conn.disconnect()
        }
    }

    private class HttpStatus(val code: Int, val body: String) : IOException("HTTP $code: $body")

    private fun str(o: JsonObject?, key: String): String? = o?.get(key)?.takeIf { it !is JsonNull }?.jsonPrimitive?.contentOrNull

    /** Throws [EngineUnavailable] with a helpful message if the model cannot be used. */
    fun check() {
        guard()
        val listing = try {
            if (api == "ollama") request("GET", "/api/tags", timeout = 5_000) else request("GET", "/v1/models", timeout = 5_000)
        } catch (e: HttpStatus) {
            throw EngineUnavailable("${this.baseUrl} answered HTTP ${e.code}; is it the right server?")
        } catch (e: IOException) {
            throw EngineUnavailable(
                if (api == "ollama") "cannot reach Ollama at ${this.baseUrl}. On the computer, start Ollama with " +
                    "OLLAMA_HOST=0.0.0.0 so the phone can reach it, and check both are on the same Wi-Fi."
                else "cannot reach a model server at ${this.baseUrl}",
            )
        }
        if (api == "ollama") {
            val names = listing["models"]?.jsonArray.orEmpty().flatMap {
                listOfNotNull(str(it.jsonObject, "name"), str(it.jsonObject, "model"))
            }.toSet()
            val want = if (':' in model) model else "$model:latest"
            if (want !in names && model !in names) {
                throw EngineUnavailable("Ollama is running but the model '$model' is not installed. On the computer run: ollama pull $model")
            }
            try {
                val info = request("POST", "/api/show", buildJsonObject { put("model", model) }, timeout = 15_000)
                val caps = info["capabilities"]?.jsonArray?.map { it.jsonPrimitive.content }
                if (caps != null && "vision" !in caps) {
                    throw EngineUnavailable("the Ollama model '$model' cannot read images; choose a vision model such as qwen3-vl:8b-instruct")
                }
                if (caps != null && "thinking" in caps) {
                    thinking = true
                    notes.add("$model is a 'thinking' build: much slower for transcription and can drop text. Prefer an -instruct tag.")
                }
            } catch (e: IOException) {
                // older Ollama without capabilities: carry on
            }
        } else {
            val ids = listing["data"]?.jsonArray.orEmpty().mapNotNull { str(it.jsonObject, "id") }
            if (ids.isEmpty()) throw EngineUnavailable("the server at ${this.baseUrl} has no model loaded")
            if (model !in ids && ids.size == 1) model = ids[0] // one-model servers answer to any name
        }
    }

    override fun ask(image: PageImageSource, prompt: String, retry: Boolean): Answer {
        guard()
        val b64 = Base64.getEncoder().encodeToString(image.png(maxSide))
        try {
            if (api == "ollama") {
                val payload = buildJsonObject {
                    put("model", model)
                    put("stream", false)
                    put("keep_alive", "15m")
                    putJsonObject("options") {
                        put("temperature", if (retry) 0.2 else 0.0)
                        put("num_ctx", context)
                        put("num_predict", maxTokens)
                        if (retry) put("repeat_penalty", 1.15)
                    }
                    if (thinking) put("think", false)
                    putJsonArray("messages") {
                        add(buildJsonObject {
                            put("role", "user")
                            put("content", prompt)
                            putJsonArray("images") { add(kotlinx.serialization.json.JsonPrimitive(b64)) }
                        })
                    }
                }
                val resp = request("POST", "/api/chat", payload)
                str(resp, "error")?.let { throw EngineError(it) }
                val message = resp["message"]?.jsonObject
                val text = str(message, "content") ?: ""
                // Everything spent on reasoning and nothing written: a failed read, not a blank page.
                val cut = str(resp, "done_reason") == "length" || (text.isBlank() && !str(message, "thinking").isNullOrEmpty())
                return Answer(text, cut)
            } else {
                val payload = buildJsonObject {
                    put("model", model)
                    put("temperature", if (retry) 0.2 else 0.0)
                    put("max_tokens", maxTokens)
                    if (retry) put("frequency_penalty", 0.3)
                    putJsonArray("messages") {
                        add(buildJsonObject {
                            put("role", "user")
                            put("content", buildJsonArray {
                                add(buildJsonObject { put("type", "text"); put("text", prompt) })
                                add(buildJsonObject {
                                    put("type", "image_url")
                                    putJsonObject("image_url") { put("url", "data:image/png;base64,$b64") }
                                })
                            })
                        })
                    }
                }
                val resp = request("POST", "/v1/chat/completions", payload)
                val choice = resp["choices"]?.jsonArray?.firstOrNull()?.jsonObject
                val text = str(choice?.get("message")?.jsonObject, "content") ?: ""
                return Answer(text, str(choice, "finish_reason") == "length")
            }
        } catch (e: HttpStatus) {
            throw EngineError("model server returned HTTP ${e.code}: ${e.body}")
        } catch (e: IOException) {
            throw EngineError("lost contact with the model server: ${e.message}", e)
        }
    }
}
