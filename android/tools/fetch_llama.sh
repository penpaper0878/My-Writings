#!/bin/sh
# Fetches the llama.cpp source the app is built and tested with into
# android/third_party/llama.cpp (a pinned commit, so builds are repeatable).
set -eu
COMMIT=46baf1f1fec5a06d1e52122a9207ca978b720f95
DEST="$(cd "$(dirname "$0")/.." && pwd)/third_party/llama.cpp"
if [ -f "$DEST/.pdf2md-commit" ] && [ "$(cat "$DEST/.pdf2md-commit")" = "$COMMIT" ]; then
    echo "llama.cpp $COMMIT already in $DEST"
    exit 0
fi
rm -rf "$DEST"
mkdir -p "$DEST"
cd "$DEST"
git init -q
git remote add origin https://github.com/ggml-org/llama.cpp.git
git fetch -q --depth 1 origin "$COMMIT"
git checkout -q FETCH_HEAD
echo "$COMMIT" > .pdf2md-commit
echo "llama.cpp $COMMIT fetched into $DEST"
