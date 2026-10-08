package io.github.penpaper0878.pdf2md

import android.graphics.BitmapFactory
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import io.github.penpaper0878.pdf2md.convert.ConversionCancelled
import io.github.penpaper0878.pdf2md.convert.Converter
import io.github.penpaper0878.pdf2md.convert.ModelStore
import io.github.penpaper0878.pdf2md.convert.OcrCache
import io.github.penpaper0878.pdf2md.convert.Reader
import io.github.penpaper0878.pdf2md.convert.Settings
import io.github.penpaper0878.pdf2md.ocr.BitmapPageImage
import io.github.penpaper0878.pdf2md.ocr.LocalModel
import io.github.penpaper0878.pdf2md.ocr.NativeLlm
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Assume.assumeTrue
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith
import java.io.File
import java.util.concurrent.atomic.AtomicBoolean
import java.util.concurrent.atomic.AtomicLong
import kotlin.test.assertFailsWith

/** The llama.cpp libraries are in the APK and answer through JNI (no model needed). */
@RunWith(AndroidJUnit4::class)
class NativeLibraryDeviceTest {
    @Test
    fun theEngineLoadsAndFindsItsCpuCode() {
        assertTrue("libpdf2md_llm.so did not load", NativeLlm.loaded)
        NativeLlm.init(ModelStore(Assets.target).nativeLibDir)
        val missing = File(Assets.target.cacheDir, "no-such-model.gguf").path
        assertEquals(0L, NativeLlm.load(missing, missing, 2, 512))
        // Past the backend check: the CPU code was found in the app's library folder.
        val error = String(NativeLlm.lastError(), Charsets.UTF_8)
        assertTrue(error, "model file could not be read" in error)
    }
}

/**
 * The real on-phone model, end to end. CI copies the model into the app's
 * files/models folder (tools/push_model_to_device.sh) and passes
 * requireModel=true; elsewhere these tests are skipped without the model.
 */
@RunWith(AndroidJUnit4::class)
class PhoneModelDeviceTest {
    private val models = ModelStore(Assets.target, File(Assets.target.filesDir, "models"))

    private fun page() = InstrumentationRegistry.getInstrumentation().context.assets
        .open("handwritten-lines.png").use { BitmapFactory.decodeStream(it)!! }

    @Before
    fun needsTheModel() {
        val ready = models.status() == ModelStore.Status.Ready
        if (InstrumentationRegistry.getArguments().getString("requireModel") == "true") {
            assertTrue("the AI model is not in ${models.dir}", ready)
        }
        assumeTrue("the AI model is not on this device", ready)
    }

    @Test
    fun readsHandwritingOnThePhone() {
        val messages = mutableListOf<String>()
        val r = Converter(
            Settings(reader = Reader.PHONE),
            OcrCache(File(Assets.target.cacheDir, "test-ocr-" + System.nanoTime())),
            models,
            progress = { synchronized(messages) { messages.add(it.message) } },
            cancelled = { false },
        ).convertImages(listOf { page() })
        val md = r.markdown
        assertEquals(listOf("phone"), r.pages.map { it.method })
        assertTrue(r.warnings.joinToString(), r.warnings.isEmpty())
        for (word in listOf("Biology", "March", "Photosynthesis", "chloroplasts", "Plants", "water", "carbon")) {
            assertTrue("'$word' not read in:\n$md", word.lowercase() in md.lowercase())
        }
        assertTrue(messages.joinToString("\n"), synchronized(messages) { messages.any { "writing" in it } })
    }

    @Test
    fun cancelStopsTheModelWhileItWrites() {
        val cancel = AtomicBoolean(false)
        val cancelledAt = AtomicLong(0L)
        val m = LocalModel(
            models.modelFile, models.mmprojFile, models.nativeLibDir,
            cancelled = { cancel.get() },
            status = { s ->
                if ("writing" in s && cancelledAt.get() == 0L) {
                    cancelledAt.set(System.nanoTime())
                    cancel.set(true)
                }
            },
        )
        try {
            // A prompt that keeps the model writing far longer than the test waits.
            assertFailsWith<InterruptedException> {
                m.ask(BitmapPageImage(page()), "Count from 1 to 2000 in words, one number per line.", retry = false)
            }
            val seconds = (System.nanoTime() - cancelledAt.get()) / 1e9
            assertTrue("took ${seconds}s to stop", seconds < 10)

            // The same model reads normally after a cancel.
            cancel.set(false)
            val again = m.ask(BitmapPageImage(page()), "What is the first word written on this page? Answer with that word only.", retry = false)
            assertTrue(again.text, "biology" in again.text.lowercase())
        } finally {
            m.close()
        }
    }

    @Test
    fun cancelDuringAConversionThrowsCancelled() {
        val cancel = AtomicBoolean(false)
        val converter = Converter(
            Settings(reader = Reader.PHONE),
            OcrCache(File(Assets.target.cacheDir, "test-ocr-" + System.nanoTime())),
            models,
            progress = { if ("looking at the page" in it.message || "writing" in it.message) cancel.set(true) },
            cancelled = { cancel.get() },
        )
        assertFailsWith<ConversionCancelled> { converter.convertImages(listOf { page() }) }
    }
}
