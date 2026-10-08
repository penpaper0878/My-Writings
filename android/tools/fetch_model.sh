#!/bin/sh
# Downloads the on-phone AI model (the same files the app downloads, see
# ModelStore.kt) into DEST, for the desktop and emulator tests.
set -eu
DEST="${1:?usage: fetch_model.sh DEST}"
BASE=https://huggingface.co/Qwen/Qwen3-VL-2B-Instruct-GGUF/resolve/main
mkdir -p "$DEST"
for name in Qwen3VL-2B-Instruct-Q4_K_M.gguf mmproj-Qwen3VL-2B-Instruct-Q8_0.gguf; do
    out="$DEST/$name"
    if [ -f "$out" ] && [ "$(head -c 4 "$out")" = "GGUF" ]; then
        echo "have $name"
        continue
    fi
    echo "downloading $name"
    curl -fL --retry 4 --retry-delay 5 -o "$out.part" "$BASE/$name"
    mv "$out.part" "$out"
    [ "$(head -c 4 "$out")" = "GGUF" ] || { echo "$name is not a GGUF file" >&2; exit 1; }
done
ls -l "$DEST"
