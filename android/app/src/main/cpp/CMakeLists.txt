# The on-phone AI reader: llama.cpp (fetched into third_party/ by
# tools/fetch_llama.sh) plus a small JNI bridge.
cmake_minimum_required(VERSION 3.22.1)
project(pdf2md_llm C CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
# Optimised even in the debug app: unoptimised, the model is unusably slow.
set(CMAKE_BUILD_TYPE Release CACHE STRING "" FORCE)

set(LLAMA_DIR "${CMAKE_CURRENT_LIST_DIR}/../../../../third_party/llama.cpp" CACHE PATH "llama.cpp source tree")
if(NOT EXISTS "${LLAMA_DIR}/CMakeLists.txt")
    message(FATAL_ERROR "llama.cpp not found at ${LLAMA_DIR}; run android/tools/fetch_llama.sh")
endif()

# ggml writes its CPU backends to CMAKE_RUNTIME_OUTPUT_DIRECTORY; they must
# land next to the other libraries to be packed into the APK.
if(NOT CMAKE_RUNTIME_OUTPUT_DIRECTORY)
    set(CMAKE_RUNTIME_OUTPUT_DIRECTORY "${CMAKE_LIBRARY_OUTPUT_DIRECTORY}")
endif()

# Only the libraries: no programs, server, tests or network code.
set(BUILD_SHARED_LIBS ON CACHE BOOL "" FORCE)
set(LLAMA_BUILD_COMMON OFF CACHE BOOL "" FORCE)
set(LLAMA_BUILD_TESTS OFF CACHE BOOL "" FORCE)
set(LLAMA_BUILD_TOOLS OFF CACHE BOOL "" FORCE)
set(LLAMA_BUILD_EXAMPLES OFF CACHE BOOL "" FORCE)
set(LLAMA_BUILD_SERVER OFF CACHE BOOL "" FORCE)
set(LLAMA_BUILD_APP OFF CACHE BOOL "" FORCE)
set(LLAMA_BUILD_MTMD ON CACHE BOOL "" FORCE)
set(LLAMA_OPENSSL OFF CACHE BOOL "" FORCE)
set(GGML_NATIVE OFF CACHE BOOL "" FORCE)
set(GGML_OPENMP OFF CACHE BOOL "" FORCE)
set(GGML_LLAMAFILE OFF CACHE BOOL "" FORCE)
# CPU code is chosen when the app runs, so each device gets the fastest
# instructions it has (arm64: dot product, int8 matrix multiply, SVE...;
# x86_64 emulators and Chromebooks: AVX2, AVX-512...).
set(GGML_BACKEND_DL ON CACHE BOOL "" FORCE)
set(GGML_CPU_ALL_VARIANTS ON CACHE BOOL "" FORCE)

add_subdirectory("${LLAMA_DIR}" llama.cpp)

add_library(pdf2md_llm SHARED pdf2md_llm.cpp llm_engine.cpp)
target_link_libraries(pdf2md_llm PRIVATE mtmd llama ggml log)
# The CPU backends are loaded at run time, not linked: build them with the app.
add_dependencies(pdf2md_llm ggml)
