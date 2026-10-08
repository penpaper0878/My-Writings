#!/bin/sh
# Copies the AI model from MODELS (see fetch_model.sh) into the debug app's
# private files/models folder on the connected device or emulator, for
# PhoneModelDeviceTest. Each file goes to /data/local/tmp with adb push, then
# into the app's folder through run-as (the debug app is debuggable); the
# shell reads the file, so the app never needs access to /data/local/tmp.
set -eu
MODELS="${1:?usage: push_model_to_device.sh MODELS}"
PKG=io.github.penpaper0878.pdf2md
adb shell run-as "$PKG" mkdir -p files/models
for f in "$MODELS"/*.gguf; do
    name=$(basename "$f")
    echo "pushing $name"
    adb push "$f" "/data/local/tmp/$name"
    adb shell "cat /data/local/tmp/$name | run-as $PKG sh -c 'cat > files/models/$name'"
    adb shell rm -f "/data/local/tmp/$name"
    want=$(wc -c < "$f" | tr -d ' ')
    got=$(adb shell run-as "$PKG" stat -c %s "files/models/$name" | tr -d '\r ')
    [ "$want" = "$got" ] || { echo "$name: copied $got of $want bytes" >&2; exit 1; }
done
adb shell run-as "$PKG" ls -l files/models
