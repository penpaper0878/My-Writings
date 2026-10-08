// Runs the app's on-phone reader (llm_engine.cpp) on a desktop with a real
// model and checks what it reads, how it stops and how it is cancelled.
//
//   probe MODEL MMPROJ IMAGE PROMPT_FILE EXPECTED_TEXT_FILE [THREADS]
//
// Exit status 0 only if every check passes.
#include <algorithm>
#include <cctype>
#include <chrono>
#include <cstdio>
#include <fstream>
#include <set>
#include <sstream>
#include <string>
#include <thread>
#include <vector>

#define STB_IMAGE_IMPLEMENTATION
#include "stb/stb_image.h"

#include "llm_engine.h"

using pdf2md::Engine;
using pdf2md::Reply;
using Clock = std::chrono::steady_clock;

static std::string read_file(const char * path) {
    std::ifstream f(path, std::ios::binary);
    std::stringstream s;
    s << f.rdbuf();
    return s.str();
}

static std::vector<std::string> words(const std::string & text) {
    std::vector<std::string> out;
    std::string w;
    for (char c : text) {
        if (std::isalnum(static_cast<unsigned char>(c))) {
            w += static_cast<char>(std::tolower(static_cast<unsigned char>(c)));
        } else if (!w.empty()) {
            out.push_back(w);
            w.clear();
        }
    }
    if (!w.empty()) out.push_back(w);
    return out;
}

static double seconds_since(Clock::time_point t) {
    return std::chrono::duration<double>(Clock::now() - t).count();
}

static const char * status_name(Reply::Status s) {
    switch (s) {
        case Reply::DONE: return "done";
        case Reply::CUT_OFF: return "cut off";
        case Reply::CANCELLED: return "cancelled";
        default: return "failed";
    }
}

int main(int argc, char ** argv) {
    if (argc < 6) {
        std::fprintf(stderr, "usage: %s MODEL MMPROJ IMAGE PROMPT_FILE EXPECTED_TEXT_FILE [THREADS]\n", argv[0]);
        return 2;
    }
    const int threads = argc > 6 ? std::atoi(argv[6]) : 4;
    int failures = 0;
    auto check = [&](bool ok, const std::string & what) {
        std::printf("%s %s\n", ok ? "PASS" : "FAIL", what.c_str());
        if (!ok) failures++;
    };

    int w = 0, h = 0, n = 0;
    unsigned char * rgb = stbi_load(argv[3], &w, &h, &n, 3);
    if (!rgb) {
        std::fprintf(stderr, "cannot read %s\n", argv[3]);
        return 2;
    }
    const std::string prompt = read_file(argv[4]);
    const std::string expected = read_file(argv[5]);

    Engine::init_backends("");
    std::string error;
    auto t0 = Clock::now();
    Engine * e = Engine::load(argv[1], argv[2], threads, 6144, error);
    if (!e) {
        std::fprintf(stderr, "load failed: %s\n", error.c_str());
        return 1;
    }
    std::printf("loaded in %.1fs\n", seconds_since(t0));
    std::printf("---- prompt as the model sees it\n%s\n----\n", e->format_prompt(prompt).c_str());

    // 1. A full page, greedy, like a first attempt.
    t0 = Clock::now();
    Reply r = e->transcribe(rgb, w, h, prompt, 3072, 1.0f, 0.0f);
    std::printf("---- transcription (%s, %.1fs, %d tokens)\n%s\n----\n",
                status_name(r.status), seconds_since(t0), e->tokens_written(), r.text.c_str());
    check(r.status == Reply::DONE, "the model finishes the page on its own");
    {
        std::vector<std::string> want = words(expected);
        std::vector<std::string> got_list = words(r.text);
        std::set<std::string> got(got_list.begin(), got_list.end());
        size_t found = 0;
        std::string missing;
        for (const auto & x : want) {
            if (got.count(x)) found++;
            else missing += " " + x;
        }
        const double recall = static_cast<double>(found) / static_cast<double>(want.size());
        std::printf("word recall %.3f (%zu of %zu); missing:%s\n", recall, found, want.size(), missing.c_str());
        check(recall >= 0.9, "at least 90% of the handwritten words are read");
        check(r.text.find("<|") == std::string::npos, "no special tokens leak into the text");
    }
    check(e->stage() == 0, "stage returns to idle");

    // 2. Running out of room is reported, not hidden.
    r = e->transcribe(rgb, w, h, prompt, 8, 1.0f, 0.0f);
    check(r.status == Reply::CUT_OFF, std::string("a reply that hits the token limit is cut off (got ") + status_name(r.status) + ")");
    check(e->tokens_written() == 8, "exactly the limit was written");

    // 3. A retry samples with a repeat penalty and still reads the page.
    r = e->transcribe(rgb, w, h, prompt, 3072, 1.15f, 0.2f);
    check(r.status == Reply::DONE && words(r.text).size() >= words(expected).size() / 2,
          "the retry settings (penalty 1.15, temperature 0.2) also read the page");

    // 4. Cancelling stops the model within seconds, while it looks at the image
    //    (the slowest step) and while it writes.
    for (bool while_writing : {false, true}) {
        Clock::time_point cancelled_at;
        std::thread canceller([&] {
            if (while_writing) {
                while (e->stage() != 2) std::this_thread::sleep_for(std::chrono::milliseconds(5));
            }
            std::this_thread::sleep_for(std::chrono::milliseconds(while_writing ? 200 : 2000));
            cancelled_at = Clock::now();
            e->set_cancelled(true);
        });
        r = e->transcribe(rgb, w, h, prompt, 3072, 1.0f, 0.0f);
        canceller.join();
        const double stop_s = seconds_since(cancelled_at);
        const char * when = while_writing ? "while writing" : "while looking at the image";
        std::printf("cancel %s: stopped %.1fs after the request\n", when, stop_s);
        check(r.status == Reply::CANCELLED, std::string("cancelled ") + when + " (got " + status_name(r.status) + ")");
        check(stop_s < 10.0, std::string("stops within 10s of Cancel ") + when);
        e->set_cancelled(false);
    }

    // 5. After a cancel the next page reads normally.
    r = e->transcribe(rgb, w, h, prompt, 3072, 1.0f, 0.0f);
    check(r.status == Reply::DONE && words(r.text).size() >= words(expected).size() / 2,
          "the next page after a cancel reads normally");

    delete e;
    stbi_image_free(rgb);
    std::printf("%s: %d failure(s)\n", failures ? "FAILED" : "OK", failures);
    return failures ? 1 : 0;
}
