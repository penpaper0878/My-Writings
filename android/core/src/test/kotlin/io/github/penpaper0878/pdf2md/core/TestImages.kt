package io.github.penpaper0878.pdf2md.core

import java.awt.Color
import java.awt.Font
import java.awt.RenderingHints
import java.awt.image.BufferedImage
import java.io.ByteArrayOutputStream
import javax.imageio.ImageIO

/** [PageImageSource] over a JVM BufferedImage, for tests (the app uses Android bitmaps). */
class BufferedPageImage(val image: BufferedImage) : PageImageSource {
    override val width get() = image.width
    override val height get() = image.height

    override fun gray(): ByteArray {
        val out = ByteArray(width * height)
        for (y in 0 until height) for (x in 0 until width) {
            val c = image.getRGB(x, y)
            val r = (c shr 16) and 0xff
            val g = (c shr 8) and 0xff
            val b = c and 0xff
            out[y * width + x] = ((r * 299 + g * 587 + b * 114) / 1000).toByte()
        }
        return out
    }

    override fun crop(top: Int, bottom: Int) = BufferedPageImage(image.getSubimage(0, top, width, bottom - top))

    private fun scaled(maxSide: Int): BufferedImage {
        val scale = maxSide.toDouble() / maxOf(width, height)
        if (scale >= 1) return image
        val w = maxOf(1, Math.round(width * scale).toInt())
        val h = maxOf(1, Math.round(height * scale).toInt())
        val out = BufferedImage(w, h, BufferedImage.TYPE_INT_RGB)
        val g = out.createGraphics()
        g.setRenderingHint(RenderingHints.KEY_INTERPOLATION, RenderingHints.VALUE_INTERPOLATION_BILINEAR)
        g.drawImage(image, 0, 0, w, h, null)
        g.dispose()
        return out
    }

    override fun png(maxSide: Int): ByteArray {
        val buf = ByteArrayOutputStream()
        ImageIO.write(scaled(maxSide), "png", buf)
        return buf.toByteArray()
    }

    override fun rgb(maxSide: Int): Triple<Int, Int, ByteArray> {
        val img = scaled(maxSide)
        val out = ByteArray(img.width * img.height * 3)
        var i = 0
        for (y in 0 until img.height) for (x in 0 until img.width) {
            val c = img.getRGB(x, y)
            out[i++] = (c shr 16).toByte(); out[i++] = (c shr 8).toByte(); out[i++] = c.toByte()
        }
        return Triple(img.width, img.height, out)
    }

    companion object {
        /** A white page with lines of black text. */
        fun text(lines: List<String>, width: Int = 1240, height: Int = 1600, size: Int = 40): BufferedPageImage {
            val img = BufferedImage(width, height, BufferedImage.TYPE_INT_RGB)
            val g = img.createGraphics()
            g.color = Color.WHITE
            g.fillRect(0, 0, width, height)
            g.color = Color(20, 20, 20)
            g.font = Font(Font.SANS_SERIF, Font.PLAIN, size)
            var y = 120
            for (line in lines) {
                if (line.isNotEmpty()) g.drawString(line, 100, y)
                y += (size * 1.8).toInt()
            }
            g.dispose()
            return BufferedPageImage(img)
        }
    }
}
