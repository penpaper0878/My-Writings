# pdf2md for Android

The [pdf2md](../pdf2md/README.md) PDF-to-Markdown converter as a phone app:
typed PDFs, scans, photos of pages and **handwritten notes**, converted on
the phone.

## Install

1. On GitHub, open **Actions → pdf2md Android app**, pick the latest green run
   and download **pdf2md-android** under *Artifacts*. Unzip it on the phone
   (or on a computer and copy it over): it holds `pdf2md.apk`.
2. Open `pdf2md.apk` on the phone. Android asks to allow installing apps from
   that source (Files, Chrome…): allow it, then install.

Needs Android 8.0 or newer on a 64-bit ARM phone (almost every phone from
recent years).

## Use

- **Choose PDF or photos**, or **Scan pages** with the camera, or share a
  PDF or photos to pdf2md from WhatsApp, Files or Gallery.
- When it finishes: **Save .md**, **Share** or **Copy**.
- Options: the languages on the pages, what the notes are about (helps with
  unclear words), which pages, and "strips" for small, dense handwriting.

Then search the result for `[?]` and `[illegible]`: those are the words
worth checking by eye.

## How pages are read

Typed pages are read from the PDF's own text, exactly as on the desktop
(the same MuPDF engine; the Markdown matches the desktop tool's). Scans,
photos and handwriting go to the reader you choose:

| Reader | Handwriting | Needs | Notes |
| --- | --- | --- | --- |
| **On this phone (AI)** | yes | a one-time 1.4 GB download | Qwen3-VL 2B on the phone. Fully offline once downloaded. Slower than a computer. It needs a few GB of free memory while it runs, so phones with 6 GB of RAM or more work best. |
| **My computer (Ollama)** | yes, best | a computer on the same Wi-Fi | The most accurate: the computer runs a larger model (the desktop tool's default, `qwen3-vl:8b-instruct`). |
| **Printed text only** | no | nothing | Google ML Kit, built into the app. Fast and fine for printed scans. |

If the chosen reader is not available (model not downloaded, computer not
reachable) the app uses printed-text reading and says so in the result.

### Using your computer

On the computer (Windows, macOS or Linux):

1. Install [Ollama](https://ollama.com) and run `ollama pull qwen3-vl:8b-instruct`.
2. Let the phone reach it: set the environment variable `OLLAMA_HOST=0.0.0.0`
   and restart Ollama. (Windows: *Settings → System → About → Advanced
   system settings → Environment Variables*; then quit Ollama from the tray
   and start it again.) Allow Ollama through the firewall when asked.
3. Find the computer's Wi-Fi address (Windows: `ipconfig`; macOS/Linux:
   `ip addr` or the network settings), e.g. `192.168.1.20`.
4. In the app choose **My computer**, enter that address and press
   **Test connection**.

## Privacy

- Typed pages and the on-phone AI never leave the phone.
- The app connects to the internet only to download the AI model (from
  Hugging Face, once) and, with **My computer**, to that computer. It refuses
  any address outside your own network, so pages are never sent to the
  internet.
- Pages read from images are cached on the phone, so converting the same
  document again is instant; *Settings → Apps → pdf2md → Storage → Clear
  cache* removes them.

## About the download being "from an unknown developer"

The APK is not from the Play Store, so Android warns before installing it.
It is signed with a key stored in this repository
(`app/signing/pdf2md-sideload.jks`), so that a newer APK installs over an
older one and keeps the downloaded model. Because that key is public, only
install pdf2md APKs you built yourself or downloaded from this repository's
own Actions runs.

## Build it yourself

You need JDK 17 and the Android SDK with NDK `28.2.13676358` and CMake
`3.31.6` (Android Studio's SDK Manager, or `sdkmanager --install
"ndk;28.2.13676358" "cmake;3.31.6"`).

```bash
cd android
tools/fetch_llama.sh                               # llama.cpp, at a pinned commit
./gradlew :app:assembleRelease                     # app/build/outputs/apk/release/
./gradlew -Ppdf2md.abis=arm64-v8a :app:assembleRelease   # phones only (faster)
```

## Tests

- `./gradlew -p core test`: the Markdown engine against the desktop tool's
  own results on real documents, byte for byte (`tools/export_parity.py`
  and `tools/export_ocr_parity.py` refresh the fixtures from the Python
  code).
- `./gradlew :app:connectedDebugAndroidTest`: on a device or emulator. MuPDF
  on Android against the desktop page data, page classification, printed
  OCR, whole conversions, and the on-phone AI model when it is on the device
  (`tools/fetch_model.sh models && tools/push_model_to_device.sh models`
  copies it into the debug app).
- `native-test/`: the on-phone AI reader's C++ built for a desktop and run
  with the real model on a handwritten page (see its `CMakeLists.txt`).

GitHub Actions runs all three on every push that touches `android/`.

## Licences

MuPDF is AGPL-3.0 (as on the desktop, through PyMuPDF), llama.cpp is MIT,
Qwen3-VL is Apache-2.0, and ML Kit is under Google's ML Kit terms.
