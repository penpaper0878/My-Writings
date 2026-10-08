package io.github.penpaper0878.pdf2md.ocr

import android.graphics.Bitmap
import io.github.penpaper0878.pdf2md.core.PageImageSource
import java.io.ByteArrayOutputStream

/** A page image on an Android bitmap. */
class BitmapPageImage(val bitmap: Bitmap) : PageImageSource {
    override val width: Int get() = bitmap.width
    override val height: Int get() = bitmap.height

    override fun gray(): ByteArray {
        val px = IntArray(width * height)
        bitmap.getPixels(px, 0, width, 0, 0, width, height)
        val out = ByteArray(px.size)
        for (i in px.indices) {
            val c = px[i]
            // PIL's "L" conversion, so strip cuts match the desktop tool.
            val l = (((c shr 16) and 0xff) * 19595 + ((c shr 8) and 0xff) * 38470 + (c and 0xff) * 7471 + 0x8000) shr 16
            out[i] = l.toByte()
        }
        return out
    }

    override fun crop(top: Int, bottom: Int): PageImageSource =
        BitmapPageImage(Bitmap.createBitmap(bitmap, 0, top, width, maxOf(1, bottom - top)))

    private fun scaled(maxSide: Int): Bitmap {
        val scale = maxSide.toDouble() / maxOf(width, height)
        if (scale >= 1) return bitmap
        return Bitmap.createScaledBitmap(
            bitmap, maxOf(1, Math.round(width * scale).toInt()), maxOf(1, Math.round(height * scale).toInt()), true,
        )
    }

    override fun png(maxSide: Int): ByteArray {
        val out = ByteArrayOutputStream()
        scaled(maxSide).compress(Bitmap.CompressFormat.PNG, 100, out)
        return out.toByteArray()
    }

    override fun rgb(maxSide: Int): Triple<Int, Int, ByteArray> {
        val b = scaled(maxSide)
        val px = IntArray(b.width * b.height)
        b.getPixels(px, 0, b.width, 0, 0, b.width, b.height)
        val out = ByteArray(px.size * 3)
        var j = 0
        for (c in px) {
            out[j++] = (c shr 16).toByte()
            out[j++] = (c shr 8).toByte()
            out[j++] = c.toByte()
        }
        return Triple(b.width, b.height, out)
    }
}
