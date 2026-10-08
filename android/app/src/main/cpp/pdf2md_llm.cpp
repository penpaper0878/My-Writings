// JNI bridge for io.github.penpaper0878.pdf2md.ocr.NativeLlm. Text crosses as
// UTF-8 byte arrays: JNI's own string functions use "modified UTF-8", which
// mangles characters outside the Basic Multilingual Plane.
#include <jni.h>

#include <android/log.h>

#include <mutex>
#include <string>

#include "ggml.h"
#include "llama.h"
#include "llm_engine.h"
#include "mtmd-helper.h"

using pdf2md::Engine;
using pdf2md::Reply;

namespace {

std::mutex g_error_mutex;
std::string g_last_error;

void set_error(const std::string & e) {
    std::lock_guard<std::mutex> lock(g_error_mutex);
    g_last_error = e;
}

void log_to_android(ggml_log_level level, const char * text, void *) {
    if (level == GGML_LOG_LEVEL_ERROR) {
        __android_log_write(ANDROID_LOG_ERROR, "pdf2md-llm", text);
    } else if (level == GGML_LOG_LEVEL_WARN) {
        __android_log_write(ANDROID_LOG_WARN, "pdf2md-llm", text);
    }
}

std::string utf8(JNIEnv * env, jbyteArray bytes) {
    const jsize n = env->GetArrayLength(bytes);
    std::string s(static_cast<size_t>(n), '\0');
    if (n > 0) env->GetByteArrayRegion(bytes, 0, n, reinterpret_cast<jbyte *>(&s[0]));
    return s;
}

std::string path(JNIEnv * env, jstring s) {
    const char * c = env->GetStringUTFChars(s, nullptr);
    std::string out(c ? c : "");
    if (c) env->ReleaseStringUTFChars(s, c);
    return out;
}

jbyteArray bytes(JNIEnv * env, const std::string & s) {
    jbyteArray a = env->NewByteArray(static_cast<jsize>(s.size()));
    if (a && !s.empty()) {
        env->SetByteArrayRegion(a, 0, static_cast<jsize>(s.size()), reinterpret_cast<const jbyte *>(s.data()));
    }
    return a;
}

Engine * engine(jlong handle) { return reinterpret_cast<Engine *>(handle); }

}  // namespace

extern "C" {

JNIEXPORT void JNICALL
Java_io_github_penpaper0878_pdf2md_ocr_NativeLlm_init(JNIEnv * env, jclass, jstring lib_dir) {
    llama_log_set(log_to_android, nullptr);
    mtmd_helper_log_set(log_to_android, nullptr);
    Engine::init_backends(path(env, lib_dir));
}

JNIEXPORT jlong JNICALL
Java_io_github_penpaper0878_pdf2md_ocr_NativeLlm_load(JNIEnv * env, jclass, jstring model, jstring mmproj,
                                                      jint threads, jint context) {
    std::string error;
    Engine * e = Engine::load(path(env, model), path(env, mmproj), threads, context, error);
    if (!e) set_error(error);
    return reinterpret_cast<jlong>(e);
}

JNIEXPORT jbyteArray JNICALL
Java_io_github_penpaper0878_pdf2md_ocr_NativeLlm_lastError(JNIEnv * env, jclass) {
    std::lock_guard<std::mutex> lock(g_error_mutex);
    return bytes(env, g_last_error);
}

// Returns a status byte (pdf2md::Reply::Status) followed by UTF-8 text.
JNIEXPORT jbyteArray JNICALL
Java_io_github_penpaper0878_pdf2md_ocr_NativeLlm_transcribe(JNIEnv * env, jclass, jlong handle, jbyteArray rgb,
                                                            jint width, jint height, jbyteArray prompt,
                                                            jint max_tokens, jfloat repeat_penalty,
                                                            jfloat temperature) {
    const jsize n = env->GetArrayLength(rgb);
    if (static_cast<long long>(n) != 3LL * width * height) {
        return bytes(env, std::string(1, static_cast<char>(Reply::FAILED)) + "bad image size");
    }
    jbyte * pixels = env->GetByteArrayElements(rgb, nullptr);
    Reply r = engine(handle)->transcribe(reinterpret_cast<const uint8_t *>(pixels), width, height,
                                         utf8(env, prompt), max_tokens, repeat_penalty, temperature);
    env->ReleaseByteArrayElements(rgb, pixels, JNI_ABORT);
    return bytes(env, std::string(1, static_cast<char>(r.status)) + r.text);
}

JNIEXPORT void JNICALL
Java_io_github_penpaper0878_pdf2md_ocr_NativeLlm_cancel(JNIEnv *, jclass, jlong handle, jboolean on) {
    engine(handle)->set_cancelled(on == JNI_TRUE);
}

// stage in the high 32 bits, tokens written in the low 32.
JNIEXPORT jlong JNICALL
Java_io_github_penpaper0878_pdf2md_ocr_NativeLlm_progress(JNIEnv *, jclass, jlong handle) {
    Engine * e = engine(handle);
    return (static_cast<jlong>(e->stage()) << 32) | static_cast<jlong>(e->tokens_written());
}

JNIEXPORT void JNICALL
Java_io_github_penpaper0878_pdf2md_ocr_NativeLlm_free(JNIEnv *, jclass, jlong handle) {
    delete engine(handle);
}

}  // extern "C"
