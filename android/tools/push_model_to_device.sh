#!/bin/sh
# Copies the AI model from MODELS (see fetch_model.sh) into the debug app's
# private files/models folder on the connected device or emulator, streamed
# through run-as (the debug app is debuggable), for PhoneModelDeviceTest.
set -eu
MODELS="${1:?usage: push_model_to_device.sh MODELS}"
PKG=io.github.penpaper0878.pdf2md
adb shell run-as "$PKG" mkdir -p files/models
for f in "$MODELS"/*.gguf; do
    name=$(basename "$f")
    echo "pushing $name"
    adb exec-in "run-as $PKG sh -c 'cat > files/models/$name'" < "$f"
    want=$(wc -c < "$f" | tr -d ' ')
    got=$(adb shell run-as "$PKG" stat -c %s "files/models/$name" | tr -d '\r ')
    [ "$want" = "$got" ] || { echo "$name: copied $got of $want bytes" >&2; exit 1; }
done
adb shell run-as "$PKG" ls -l files/models
