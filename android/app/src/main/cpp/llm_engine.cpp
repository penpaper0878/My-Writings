// Follows llama.cpp's own mtmd-cli for the image -> text flow: format the chat
// prompt with the media marker, tokenize it together with the image, evaluate
// the chunks, then sample one token at a time.
#include "llm_engine.h"

#include <cstring>
#include <memory>
#include <mutex>
#include <vector>

#include "ggml-backend.h"
#include "llama.h"
#include "mtmd-helper.h"
#include "mtmd.h"

namespace pdf2md {

namespace {

// Ollama's defaults, so a retry here samples like a retry on the desktop.
constexpr int kPenaltyLastN = 64;
constexpr int kTopK = 40;
constexpr float kTopP = 0.9f;
constexpr uint32_t kSeed = 42;

struct ChunksDeleter { void operator()(mtmd_input_chunks * c) const { mtmd_input_chunks_free(c); } };
struct BitmapDeleter { void operator()(mtmd_bitmap * b) const { mtmd_bitmap_free(b); } };
struct SamplerDeleter { void operator()(llama_sampler * s) const { llama_sampler_free(s); } };

// Resets the stage counter however transcribe() returns.
struct StageGuard {
    std::atomic<int> & stage;
    ~StageGuard() { stage.store(0); }
};

}  // namespace

void Engine::init_backends(const std::string & lib_dir) {
    static std::once_flag once;
    std::call_once(once, [&] {
        if (lib_dir.empty()) {
            ggml_backend_load_all();
        } else {
            ggml_backend_load_all_from_path(lib_dir.c_str());
        }
        llama_backend_init();
    });
}

bool Engine::abort_requested(void * self) {
    return static_cast<Engine *>(self)->cancelled_.load();
}

Engine * Engine::load(const std::string & model_path, const std::string & mmproj_path,
                      int threads, int context, std::string & error) {
    if (ggml_backend_dev_count() == 0) {
        error = "no compute backend could be loaded on this device";
        return nullptr;
    }
    std::unique_ptr<Engine> e(new Engine());

    llama_model_params mp = llama_model_default_params();
    mp.n_gpu_layers = 0;
    e->model_ = llama_model_load_from_file(model_path.c_str(), mp);
    if (!e->model_) {
        error = "the model file could not be read (damaged download?)";
        return nullptr;
    }

    llama_context_params cp = llama_context_default_params();
    cp.n_ctx = static_cast<uint32_t>(context);
    cp.n_batch = 1024;
    cp.n_ubatch = 512;
    cp.n_threads = threads;
    cp.n_threads_batch = threads;
    cp.no_perf = true;
    cp.abort_callback = abort_requested;
    cp.abort_callback_data = e.get();
    e->ctx_ = llama_init_from_model(e->model_, cp);
    if (!e->ctx_) {
        error = "not enough memory to run the model";
        return nullptr;
    }
    e->n_ctx_ = static_cast<int>(llama_n_ctx(e->ctx_));
    e->n_batch_ = static_cast<int>(cp.n_batch);

    mtmd_context_params vp = mtmd_context_params_default();
    vp.use_gpu = false;
    vp.n_threads = threads;
    vp.print_timings = false;
    vp.warmup = false;
    e->vision_ = mtmd_init_from_file(mmproj_path.c_str(), e->model_, vp);
    if (!e->vision_) {
        error = "the image part of the model (mmproj) could not be read";
        return nullptr;
    }
    if (!mtmd_support_vision(e->vision_)) {
        error = "this model cannot read images";
        return nullptr;
    }
    return e.release();
}

Engine::~Engine() {
    if (vision_) mtmd_free(vision_);
    if (ctx_) llama_free(ctx_);
    if (model_) llama_model_free(model_);
}

std::string Engine::format_prompt(const std::string & prompt) const {
    // The image goes first, then the instructions: how the model was trained.
    const std::string content = std::string(mtmd_default_marker()) + prompt;
    llama_chat_message msg{"user", content.c_str()};
    const char * tmpl = llama_model_chat_template(model_, nullptr);
    std::vector<char> buf(content.size() * 2 + 256);
    int32_t n = llama_chat_apply_template(tmpl, &msg, 1, true, buf.data(), static_cast<int32_t>(buf.size()));
    if (n > static_cast<int32_t>(buf.size())) {
        buf.resize(static_cast<size_t>(n));
        n = llama_chat_apply_template(tmpl, &msg, 1, true, buf.data(), static_cast<int32_t>(buf.size()));
    }
    if (n < 0) {
        // A template llama.cpp does not know: ChatML, which Qwen models use.
        return "<|im_start|>user\n" + content + "<|im_end|>\n<|im_start|>assistant\n";
    }
    return std::string(buf.data(), static_cast<size_t>(n));
}

Reply Engine::transcribe(const uint8_t * rgb, int width, int height, const std::string & prompt,
                         int max_tokens, float repeat_penalty, float temperature) {
    Reply reply;
    StageGuard guard{stage_};
    written_.store(0);
    stage_.store(1);
    if (cancelled_.load()) {
        reply.status = Reply::CANCELLED;
        return reply;
    }

    llama_memory_clear(llama_get_memory(ctx_), true);

    std::unique_ptr<mtmd_bitmap, BitmapDeleter> bitmap(
        mtmd_bitmap_init(static_cast<uint32_t>(width), static_cast<uint32_t>(height), rgb));
    std::unique_ptr<mtmd_input_chunks, ChunksDeleter> chunks(mtmd_input_chunks_init());
    const std::string text = format_prompt(prompt);
    mtmd_input_text input{text.c_str(), text.size(), /*add_special=*/true, /*parse_special=*/true};
    const mtmd_bitmap * bitmaps[] = {bitmap.get()};
    if (mtmd_tokenize(vision_, chunks.get(), &input, bitmaps, 1) != 0) {
        reply.text = "the page image could not be prepared for the model";
        return reply;
    }

    const int prompt_pos = static_cast<int>(mtmd_helper_get_n_pos(chunks.get()));
    if (prompt_pos + 16 > n_ctx_) {
        reply.text = "the page image is too large for the model's memory; use more strips";
        return reply;
    }

    llama_pos n_past = 0;
    if (mtmd_helper_eval_chunks(vision_, ctx_, chunks.get(), 0, 0, n_batch_, true, &n_past) != 0) {
        if (cancelled_.load()) {
            reply.status = Reply::CANCELLED;
        } else {
            reply.text = "the model could not read the page image";
        }
        return reply;
    }

    const llama_vocab * vocab = llama_model_get_vocab(model_);
    llama_sampler_chain_params sp = llama_sampler_chain_default_params();
    sp.no_perf = true;
    std::unique_ptr<llama_sampler, SamplerDeleter> sampler(llama_sampler_chain_init(sp));
    if (repeat_penalty > 1.0f) {
        llama_sampler_chain_add(sampler.get(), llama_sampler_init_penalties(
            llama_vocab_n_tokens(vocab), kPenaltyLastN, repeat_penalty, 0.0f, 0.0f));
    }
    if (temperature > 0.0f) {
        llama_sampler_chain_add(sampler.get(), llama_sampler_init_top_k(kTopK));
        llama_sampler_chain_add(sampler.get(), llama_sampler_init_top_p(kTopP, 1));
        llama_sampler_chain_add(sampler.get(), llama_sampler_init_temp(temperature));
        llama_sampler_chain_add(sampler.get(), llama_sampler_init_dist(kSeed));
    } else {
        llama_sampler_chain_add(sampler.get(), llama_sampler_init_greedy());
    }

    stage_.store(2);
    llama_batch batch = llama_batch_init(1, 0, 1);
    std::string out;
    char piece[256];
    bool finished = false;
    bool failed = false;
    for (int i = 0; i < max_tokens; i++) {
        if (cancelled_.load()) break;
        const llama_token token = llama_sampler_sample(sampler.get(), ctx_, -1);
        if (llama_vocab_is_eog(vocab, token)) {
            finished = true;
            break;
        }
        const int32_t n = llama_token_to_piece(vocab, token, piece, sizeof(piece), 0, false);
        if (n > 0) out.append(piece, static_cast<size_t>(n));
        written_.store(i + 1);
        if (n_past + 1 >= n_ctx_) break;  // out of room: reported as cut off

        batch.n_tokens = 1;
        batch.token[0] = token;
        batch.pos[0] = n_past++;
        batch.n_seq_id[0] = 1;
        batch.seq_id[0][0] = 0;
        batch.logits[0] = 1;
        if (llama_decode(ctx_, batch) != 0) {
            failed = !cancelled_.load();
            break;
        }
    }
    llama_batch_free(batch);

    if (cancelled_.load()) {
        reply.status = Reply::CANCELLED;
    } else if (failed) {
        reply.text = "the model stopped with an error while writing";
    } else {
        reply.status = finished ? Reply::DONE : Reply::CUT_OFF;
        reply.text = std::move(out);
    }
    return reply;
}

}  // namespace pdf2md
