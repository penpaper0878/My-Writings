# Desktop build of the app's on-phone reader, for testing it with a real model:
#   cmake -S android/native-test -B build -DLLAMA_DIR=android/third_party/llama.cpp
#   cmake --build build -j && build/probe MODEL MMPROJ IMAGE PROMPT EXPECTED
# Built like the app: shared libraries, CPU code chosen when it runs.
cmake_minimum_required(VERSION 3.22)
project(pdf2md_probe C CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
if(NOT CMAKE_BUILD_TYPE)
    set(CMAKE_BUILD_TYPE Release)
endif()
set(CMAKE_RUNTIME_OUTPUT_DIRECTORY "${CMAKE_BINARY_DIR}/bin")
set(CMAKE_LIBRARY_OUTPUT_DIRECTORY "${CMAKE_BINARY_DIR}/bin")

set(LLAMA_DIR "${CMAKE_CURRENT_LIST_DIR}/../third_party/llama.cpp" CACHE PATH "llama.cpp source tree")
set(APP_CPP "${CMAKE_CURRENT_LIST_DIR}/../app/src/main/cpp")

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
set(GGML_BACKEND_DL ON CACHE BOOL "" FORCE)
set(GGML_CPU_ALL_VARIANTS ON CACHE BOOL "" FORCE)

add_subdirectory("${LLAMA_DIR}" llama.cpp)

add_executable(probe probe.cpp "${APP_CPP}/llm_engine.cpp")
target_include_directories(probe PRIVATE "${APP_CPP}")
target_link_libraries(probe PRIVATE mtmd llama ggml vendor::stb)
add_dependencies(probe ggml)
