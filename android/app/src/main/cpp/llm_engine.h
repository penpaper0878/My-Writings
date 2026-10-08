// A vision model that reads one page image and writes its text, built on
// llama.cpp's multimodal library (mtmd). Plain C++ with no JNI, so the same
// code runs in the app and in the desktop test that uses the real model.
#pragma once

#include <atomic>
#include <cstdint>
#include <string>

struct ggml_tensor;
struct llama_model;
struct llama_context;
struct mtmd_context;

namespace pdf2md {

struct Reply {
    enum Status { DONE = 0, CUT_OFF = 1, CANCELLED = 2, FAILED = 3 };
    Status status = FAILED;
    std::string text;   // UTF-8; the error message when FAILED
};

class Engine {
public:
    // Loads the compute backends found in `lib_dir` (empty: next to the
    // program). Call once, before the first load().
    static void init_backends(const std::string & lib_dir);

    // nullptr on failure, with the reason in `error`.
    static Engine * load(const std::string & model_path, const std::string & mmproj_path,
                         int threads, int context, std::string & error);
    ~Engine();

    // Greedy when temperature <= 0. Not thread-safe: one call at a time.
    Reply transcribe(const uint8_t * rgb, int width, int height, const std::string & prompt,
                     int max_tokens, float repeat_penalty, float temperature);

    // Safe to call from any thread while transcribe() runs.
    void set_cancelled(bool on) { cancelled_.store(on); }
    // 0 idle, 1 looking at the image, 2 writing.
    int stage() const { return stage_.load(); }
    int tokens_written() const { return written_.load(); }

    // The prompt exactly as the model sees it (for tests).
    std::string format_prompt(const std::string & prompt) const;

private:
    Engine() = default;
    static bool abort_requested(void * self);
    static bool vision_checkpoint(ggml_tensor * t, bool ask, void * self);

    llama_model * model_ = nullptr;
    llama_context * ctx_ = nullptr;
    mtmd_context * vision_ = nullptr;
    int n_ctx_ = 0;
    int n_batch_ = 0;
    std::atomic<bool> cancelled_{false};
    std::atomic<int> stage_{0};
    std::atomic<int> written_{0};
    unsigned vision_nodes_ = 0;  // only touched by the thread running the encoder
};

}  // namespace pdf2md
