package io.github.penpaper0878.pdf2md.core

import com.sun.net.httpserver.HttpServer
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.boolean
import kotlinx.serialization.json.buildJsonArray
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.double
import kotlinx.serialization.json.int
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import kotlinx.serialization.json.put
import java.io.ByteArrayInputStream
import java.net.InetSocketAddress
import java.util.Base64
import javax.imageio.ImageIO
import kotlin.test.AfterTest
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFailsWith
import kotlin.test.assertTrue

/** A stand-in for Ollama / an OpenAI-compatible server. */
class MockServer {
    val requests = mutableListOf<Triple<String, JsonObject, Map<String, String>>>()
    var models = listOf("qwen2.5vl:7b", "qwen3-vl:8b-instruct")
    var capabilities = listOf("completion", "vision")
    var reply: (JsonObject) -> String = { "# Page\n\nHello from the model." }
    var finish = "stop"
    var thinking = ""
    private val server = HttpServer.create(InetSocketAddress("127.0.0.1", 0), 0)
    val url get() = "http://127.0.0.1:${server.address.port}"

    init {
        server.createContext("/") { ex ->
            val path = ex.requestURI.path
            val body = ex.requestBody.readBytes().toString(Charsets.UTF_8)
            val out: String = when {
                ex.requestMethod == "GET" && path == "/api/tags" ->
                    buildJsonObject { put("models", buildJsonArray { models.forEach { add(buildJsonObject { put("name", it); put("model", it) }) } }) }.toString()
                ex.requestMethod == "GET" && path == "/v1/models" ->
                    buildJsonObject { put("data", buildJsonArray { models.forEach { add(buildJsonObject { put("id", it) }) } }) }.toString()
                path == "/api/show" ->
                    buildJsonObject { put("capabilities", buildJsonArray { capabilities.forEach { add(kotlinx.serialization.json.JsonPrimitive(it)) } }) }.toString()
                else -> {
                    val req = Json.parseToJsonElement(body).jsonObject
                    synchronized(requests) {
                        requests.add(Triple(path, req, ex.requestHeaders.mapValues { it.value.first() }))
                    }
                    val text = reply(req)
                    if (path == "/api/chat") buildJsonObject {
                        put("message", buildJsonObject {
                            put("role", "assistant"); put("content", text)
                            if (thinking.isNotEmpty()) put("thinking", thinking)
                        })
                        put("done_reason", finish)
                    }.toString()
                    else buildJsonObject {
                        put("choices", buildJsonArray {
                            add(buildJsonObject { put("message", buildJsonObject { put("content", text) }); put("finish_reason", finish) })
                        })
                    }.toString()
                }
            }
            val bytes = out.toByteArray()
            ex.sendResponseHeaders(200, bytes.size.toLong())
            ex.responseBody.use { it.write(bytes) }
        }
        server.start()
    }

    fun close() = server.stop(0)
}

class RemoteModelTest {
    private val server = MockServer()

    @AfterTest
    fun stop() = server.close()

    private fun page() = BufferedPageImage.text(listOf("First line of notes", "", "Second paragraph here"))

    private fun model(): RemoteModel {
        val m = RemoteModel("qwen2.5vl:7b", baseUrl = server.url)
        m.check()
        return m
    }

    private fun content(i: Int) = server.requests[i].second["messages"]!!.jsonArray[0].jsonObject["content"]!!.jsonPrimitive.content

    @Test
    fun ollamaRequest() {
        server.reply = { "# Notes\n\nFirst line of notes" }
        val result = Transcriber(model()).transcribe(page(), 1)
        assertEquals("# Notes\n\nFirst line of notes\n", result.markdown)
        assertEquals(emptyList(), result.warnings)
        val (path, body, _) = server.requests[0]
        assertEquals("/api/chat", path)
        assertEquals("qwen2.5vl:7b", body["model"]!!.jsonPrimitive.content)
        assertEquals(false, body["stream"]!!.jsonPrimitive.boolean)
        val options = body["options"]!!.jsonObject
        assertEquals(0.0, options["temperature"]!!.jsonPrimitive.double)
        assertTrue(options["num_ctx"]!!.jsonPrimitive.int >= 8192)
        assertTrue(content(0).startsWith("Transcribe this page into Markdown"))
        val img = body["messages"]!!.jsonArray[0].jsonObject["images"]!!.jsonArray[0].jsonPrimitive.content
        val png = Base64.getDecoder().decode(img)
        assertEquals(listOf(0x89, 0x50, 0x4E, 0x47), png.take(4).map { it.toInt() and 0xff })
    }

    @Test
    fun chattyAnswerIsCleaned() {
        server.reply = { "Here is the transcription of the page:\n\n```markdown\n# Hi\n\nText\n```" }
        assertEquals("# Hi\n\nText\n", Transcriber(model()).transcribe(page(), 1).markdown)
    }

    @Test
    fun loopingAnswerIsRetriedInStrips() {
        var calls = 0
        server.reply = { calls += 1; if (calls == 1) "Notes\n" + "the the the the ".repeat(60) else "Part ${calls - 1} text." }
        val result = Transcriber(model()).transcribe(page(), 1)
        assertEquals(3, server.requests.size)
        assertTrue("part 1 of 2" in content(1))
        assertTrue("part 2 of 2" in content(2))
        assertTrue(server.requests[1].second["options"]!!.jsonObject["repeat_penalty"]!!.jsonPrimitive.double > 1)
        assertEquals("Part 1 text.\n\nPart 2 text.\n", result.markdown)
        assertEquals(emptyList(), result.warnings)
    }

    @Test
    fun persistentLoopIsCollapsedWithWarning() {
        server.reply = { "Real text.\n" + List(30) { "same line" }.joinToString("\n") }
        val result = Transcriber(model()).transcribe(page(), 4)
        assertTrue("same line\nsame line" !in result.markdown)
        assertTrue("Real text." in result.markdown)
        assertTrue(result.warnings.any { "page 4" in it && "repeated" in it })
    }

    @Test
    fun truncatedAnswerIsRetried() {
        server.finish = "length"
        val result = Transcriber(model()).transcribe(page(), 2)
        assertEquals(3, server.requests.size)
        assertTrue(result.warnings.isNotEmpty())
    }

    @Test
    fun missingModelExplainsHowToGetIt() {
        server.models = listOf("llama3.2:3b")
        val e = assertFailsWith<EngineUnavailable> { RemoteModel("qwen3-vl:8b-instruct", baseUrl = server.url).check() }
        assertTrue("ollama pull qwen3-vl:8b-instruct" in e.message!!)
    }

    @Test
    fun modelWithoutVisionIsRejected() {
        server.capabilities = listOf("completion")
        val e = assertFailsWith<EngineUnavailable> { RemoteModel("qwen2.5vl:7b", baseUrl = server.url).check() }
        assertTrue("cannot read images" in e.message!!)
    }

    @Test
    fun untaggedNameMatchesLatest() {
        server.models = listOf("minicpm-v:latest")
        RemoteModel("minicpm-v", baseUrl = server.url).check()
    }

    @Test
    fun serverNotRunningIsExplained() {
        val e = assertFailsWith<EngineUnavailable> { RemoteModel("x", baseUrl = "http://127.0.0.1:9").check() }
        assertTrue("OLLAMA_HOST=0.0.0.0" in e.message!!)
    }

    @Test
    fun internetServerIsRefused() {
        val e = assertFailsWith<EngineUnavailable> { RemoteModel("x", baseUrl = "http://8.8.8.8:11434").check() }
        assertTrue("local network" in e.message!!)
    }

    @Test
    fun openAiCompatibleServer() {
        server.models = listOf("olmOCR-7B-0725-Q4_K_M.gguf")
        server.reply = { "Transcribed." }
        val m = RemoteModel("anything", api = "openai", baseUrl = server.url + "/v1", apiKey = "sekret")
        m.check()
        assertEquals("olmOCR-7B-0725-Q4_K_M.gguf", m.model)
        assertEquals("Transcribed.\n", Transcriber(m).transcribe(page(), 1).markdown)
        val (path, body, headers) = server.requests[0]
        assertEquals("/v1/chat/completions", path)
        assertEquals(0.0, body["temperature"]!!.jsonPrimitive.double)
        val parts = body["messages"]!!.jsonArray[0].jsonObject["content"]!!.jsonArray
        assertEquals("text", parts[0].jsonObject["type"]!!.jsonPrimitive.content)
        assertTrue(parts[1].jsonObject["image_url"]!!.jsonObject["url"]!!.jsonPrimitive.content.startsWith("data:image/png;base64,"))
        assertEquals("Bearer sekret", headers["Authorization"])
    }

    @Test
    fun imageIsDownscaledToMaxSide() {
        val m = RemoteModel("qwen2.5vl:7b", baseUrl = server.url, maxSide = 512)
        m.check()
        Transcriber(m).transcribe(BufferedPageImage.text(listOf("x"), 2000, 3000), 1)
        val img = server.requests[0].second["messages"]!!.jsonArray[0].jsonObject["images"]!!.jsonArray[0].jsonPrimitive.content
        val decoded = ImageIO.read(ByteArrayInputStream(Base64.getDecoder().decode(img)))
        assertEquals(512, maxOf(decoded.width, decoded.height))
    }

    @Test
    fun thinkingBuildIsFlaggedAndToldNotToThink() {
        server.capabilities = listOf("completion", "vision", "thinking")
        val m = model()
        assertTrue(m.notes.isNotEmpty() && "instruct" in m.notes[0])
        Transcriber(m).transcribe(page(), 1)
        assertEquals(false, server.requests[0].second["think"]!!.jsonPrimitive.boolean)
    }

    @Test
    fun answerLostInReasoningIsNotABlankPage() {
        server.reply = { "" }
        server.thinking = "Let me look at the page carefully... ".repeat(50)
        val result = Transcriber(model()).transcribe(page(), 7)
        assertEquals(3, server.requests.size)
        assertTrue(result.warnings.any { "page 7" in it })
    }

    @Test
    fun languagesAndHintReachThePrompt() {
        Transcriber(model(), lang = "guj+eng", hint = "physics").transcribe(page(), 1)
        assertTrue("written in Gujarati and English" in content(0))
        assertTrue("physics" in content(0))
    }

    @Test
    fun localAddresses() {
        for ((url, local) in listOf(
            "http://localhost:11434" to true, "http://127.0.0.1:8080" to true, "http://[::1]:11434" to true,
            "http://192.168.1.20:11434" to true, "http://10.0.0.5:1234" to true, "http://172.20.1.1" to true,
            "http://gpu-box.local:11434" to true, "http://8.8.8.8:11434" to false, "http://172.32.0.1" to false,
        )) assertEquals(local, RemoteModel.isLocalUrl(url), url)
    }

    @Test
    fun baseUrlNormalisation() {
        assertEquals("http://192.168.1.5:11434", RemoteModel.normalizeBase("192.168.1.5", "ollama"))
        assertEquals("http://192.168.1.5:1234", RemoteModel.normalizeBase("http://192.168.1.5:1234/v1/", "openai"))
    }
}
