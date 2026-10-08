package io.github.penpaper0878.pdf2md.convert

import android.content.Context

/** How scanned and handwritten pages are read. */
enum class Reader { PHONE, COMPUTER, PRINTED }

data class Settings(
    val reader: Reader = Reader.PHONE,
    val computerUrl: String = "",
    val computerModel: String = DEFAULT_COMPUTER_MODEL,
    val lang: String = "",
    val hint: String = "",
    val pages: String = "",
    val mode: String = "auto", // auto | digital | ocr
    val tiles: Int = 1,
    val pageMarkers: Boolean = false,
) {
    companion object {
        const val DEFAULT_COMPUTER_MODEL = "qwen3-vl:8b-instruct"
    }
}

class SettingsStore(context: Context) {
    private val prefs = context.getSharedPreferences("settings", Context.MODE_PRIVATE)

    fun load() = Settings(
        reader = runCatching { Reader.valueOf(prefs.getString("reader", Reader.PHONE.name)!!) }.getOrDefault(Reader.PHONE),
        computerUrl = prefs.getString("computerUrl", "")!!,
        computerModel = prefs.getString("computerModel", Settings.DEFAULT_COMPUTER_MODEL)!!,
        lang = prefs.getString("lang", "")!!,
        hint = prefs.getString("hint", "")!!,
        pages = "", // per document, not remembered
        mode = prefs.getString("mode", "auto")!!,
        tiles = prefs.getInt("tiles", 1),
        pageMarkers = prefs.getBoolean("pageMarkers", false),
    )

    fun save(s: Settings) {
        prefs.edit()
            .putString("reader", s.reader.name)
            .putString("computerUrl", s.computerUrl)
            .putString("computerModel", s.computerModel)
            .putString("lang", s.lang)
            .putString("hint", s.hint)
            .putString("mode", s.mode)
            .putInt("tiles", s.tiles)
            .putBoolean("pageMarkers", s.pageMarkers)
            .apply()
    }
}
