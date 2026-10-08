package io.github.penpaper0878.pdf2md.convert

import android.app.DownloadManager
import android.content.Context
import android.net.Uri
import java.io.File

/**
 * The on-phone AI model: Qwen3-VL 2B Instruct (the small sibling of the
 * desktop default), about 1.5 GB in two files, downloaded once from Hugging
 * Face with Android's download manager (resumes by itself, shows a
 * notification). After that the phone needs no network to read handwriting.
 */
class ModelStore(private val context: Context) {
    class ModelFile(val name: String, val url: String, val approxBytes: Long)

    val files = listOf(
        ModelFile(
            "Qwen3VL-2B-Instruct-Q4_K_M.gguf",
            "https://huggingface.co/Qwen/Qwen3-VL-2B-Instruct-GGUF/resolve/main/Qwen3VL-2B-Instruct-Q4_K_M.gguf",
            1_056L * 1024 * 1024,
        ),
        ModelFile(
            "mmproj-Qwen3VL-2B-Instruct-Q8_0.gguf",
            "https://huggingface.co/Qwen/Qwen3-VL-2B-Instruct-GGUF/resolve/main/mmproj-Qwen3VL-2B-Instruct-Q8_0.gguf",
            424L * 1024 * 1024,
        ),
    )

    val dir: File = (context.getExternalFilesDir("models") ?: File(context.filesDir, "models")).also { it.mkdirs() }
    val modelFile get() = File(dir, files[0].name)
    val mmprojFile get() = File(dir, files[1].name)
    val totalBytes get() = files.sumOf { it.approxBytes }

    private val prefs = context.getSharedPreferences("models", Context.MODE_PRIVATE)
    private val dm get() = context.getSystemService(DownloadManager::class.java)

    sealed class Status {
        data object Missing : Status()
        data class Downloading(val done: Long, val total: Long) : Status()
        data class Failed(val reason: String) : Status()
        data object Ready : Status()
    }

    private fun isGguf(f: File): Boolean = f.exists() && f.length() > 50L * 1024 * 1024 &&
        runCatching { f.inputStream().use { s -> ByteArray(4).also { s.read(it) } } }.getOrNull()
            ?.contentEquals("GGUF".toByteArray()) == true

    fun status(): Status {
        var done = 0L
        var total = 0L
        var downloading = false
        for (f in files) {
            val id = prefs.getLong(f.name, -1L)
            val local = File(dir, f.name)
            if (id >= 0) {
                dm.query(DownloadManager.Query().setFilterById(id)).use { c ->
                    if (c.moveToFirst()) {
                        val st = c.getInt(c.getColumnIndexOrThrow(DownloadManager.COLUMN_STATUS))
                        val got = c.getLong(c.getColumnIndexOrThrow(DownloadManager.COLUMN_BYTES_DOWNLOADED_SO_FAR))
                        val size = c.getLong(c.getColumnIndexOrThrow(DownloadManager.COLUMN_TOTAL_SIZE_BYTES))
                        when (st) {
                            DownloadManager.STATUS_SUCCESSFUL -> {
                                prefs.edit().remove(f.name).apply()
                                done += local.length(); total += local.length()
                            }
                            DownloadManager.STATUS_FAILED -> {
                                val reason = c.getInt(c.getColumnIndexOrThrow(DownloadManager.COLUMN_REASON))
                                prefs.edit().remove(f.name).apply()
                                local.delete()
                                return Status.Failed("download failed (code $reason); check the internet connection and try again")
                            }
                            else -> {
                                downloading = true
                                done += maxOf(got, 0L); total += if (size > 0) size else f.approxBytes
                            }
                        }
                    } else {
                        prefs.edit().remove(f.name).apply()
                    }
                }
            } else if (isGguf(local)) {
                done += local.length(); total += local.length()
            } else {
                total += f.approxBytes
            }
        }
        if (downloading) return Status.Downloading(done, total)
        return if (files.all { isGguf(File(dir, it.name)) && prefs.getLong(it.name, -1L) < 0 }) Status.Ready else Status.Missing
    }

    fun download(wifiOnly: Boolean) {
        for (f in files) {
            if (prefs.getLong(f.name, -1L) >= 0 || isGguf(File(dir, f.name))) continue
            File(dir, f.name).delete()
            val req = DownloadManager.Request(Uri.parse(f.url))
                .setTitle("pdf2md AI model")
                .setDescription(f.name)
                .setNotificationVisibility(DownloadManager.Request.VISIBILITY_VISIBLE_NOTIFY_COMPLETED)
                .setDestinationInExternalFilesDir(context, "models", f.name)
                .setAllowedOverMetered(!wifiOnly)
                .setAllowedOverRoaming(false)
            prefs.edit().putLong(f.name, dm.enqueue(req)).apply()
        }
    }

    fun remove() {
        for (f in files) {
            val id = prefs.getLong(f.name, -1L)
            if (id >= 0) dm.remove(id)
            prefs.edit().remove(f.name).apply()
            File(dir, f.name).delete()
        }
    }
}
