# pdf2md — any PDF to Markdown, offline

Converts PDFs to clean Markdown on your own computer: typed documents, scanned
books, phone photos of notes, and **handwritten notes**. Nothing is uploaded
anywhere.

```
pdf2md notes.pdf          →  notes.md  (+ notes_assets/ with its images)
```

## How it stays accurate

Every page is looked at on its own and read in the most exact way available:

| The page is… | pdf2md reads it with… | Accuracy |
| --- | --- | --- |
| Typed (Word, LaTeX, exported PDFs) | the PDF's own text layer: **no OCR, no guessing** | exact characters |
| Scanned, photographed, or a scanner-app PDF | a vision model on your machine, or Tesseract | as good as the model |
| Handwritten (paper scans, GoodNotes / Notability / OneNote ink) | a vision model on your machine | as good as the model |
| Typed with handwriting on top (annotated handouts) | a vision model, so both are read | as good as the model |
| Blank | skipped | — |

Details that matter for precision:

- **Typed pages keep their structure.** Headings come from font sizes measured
  across the whole document, not per page. Bold, italic, strikethrough, code,
  links, nested bullet and numbered lists, ruled tables, images and vector
  charts are all kept. Two-column papers read column by column; forms read
  row by row.
- **Nothing is invented on typed pages.** Line-end hyphens are undone using the
  document's own vocabulary (so "well-known" stays hyphenated if the document
  writes it that way). Accents split apart by TeX (`na¨ıve`) are rebuilt.
  Running headers, footers and page numbers are removed only when they repeat,
  and chapter or slide titles are never removed.
- **Broken text layers are caught.** Some PDFs have text that copies out as
  garbage (fonts without a Unicode map), and scanner apps add hidden OCR
  layers that are poor on handwriting. pdf2md detects both and reads those
  pages from the image instead.
- **The vision model is told to transcribe, not interpret.** It must copy words
  exactly, keep every script as written (no transliteration), write `[?]`
  after a word it is unsure of and `[illegible]` for one it cannot read,
  put maths in LaTeX, and turn tables into Markdown tables.
- **Model failures are caught.** If the model loops or runs out of room, the
  page is re-read in strips. Blank pages are never sent (a model asked to read
  nothing tends to invent something). Every page that could not be read is
  marked in the output and in a warning, never silently dropped.

## Install

You need **Python 3.9+**. From this folder:

```bash
pip install .
pdf2md --check
```

That alone converts typed PDFs. For scans and handwriting, add a vision model
(below). `pdf2md --check` always tells you what is ready and what is missing.

### Handwriting and scans: a local vision model (recommended)

1. Install **Ollama** from <https://ollama.com> (Windows, macOS, Linux).
2. Download a model once (about 6 GB). After that no internet is needed:

   ```bash
   ollama pull qwen3-vl:8b-instruct
   ```

3. Run `pdf2md --check` again. It should say `Ready for everything`.

Which model:

| Your computer | Model | Notes |
| --- | --- | --- |
| 16 GB RAM, or a GPU with 8 GB or more | `qwen3-vl:8b-instruct` (default) | best balance; strong on handwriting, tables and maths |
| 8 GB RAM | a smaller `-instruct` tag of `qwen3-vl`, e.g. `qwen3-vl:4b-instruct` | faster, a little less accurate |
| 32 GB RAM or a 24 GB GPU | the largest `qwen3-vl` `-instruct` tag you can run | most accurate |
| already have it | `qwen2.5vl:7b` | older, still good |

Use `--model NAME` to choose one (or set `PDF2MD_MODEL`). **Pick an
`-instruct` tag.** Ollama's plain `qwen3-vl:<size>` tags are "thinking" builds,
which are many times slower at transcription and can drop text on dense pages.
pdf2md warns you if you choose one.

**LM Studio, llama.cpp (`llama-server`), vLLM** and other OpenAI-compatible
servers work too, including OCR-specialised models you load there:

```bash
pdf2md notes.pdf --api openai --base-url http://127.0.0.1:1234   # LM Studio
pdf2md notes.pdf --api openai --base-url http://127.0.0.1:8080   # llama-server
```

### Printed scans without a model: Tesseract

Tesseract is small and fast, and very good on clean printed pages. It is not
built for handwriting. pdf2md uses it automatically when no vision model is
running.

- Ubuntu/Debian: `sudo apt install tesseract-ocr` (languages:
  `tesseract-ocr-hin`, `tesseract-ocr-guj`, …)
- macOS: `brew install tesseract tesseract-lang`
- Windows: the installer from <https://github.com/UB-Mannheim/tesseract/wiki>

## Use

```bash
pdf2md notes.pdf                        # writes notes.md next to it
pdf2md notes.pdf -o out/notes.md        # choose where
pdf2md scans/ -o markdown/ -r           # a whole folder, sub-folders too
pdf2md book.pdf --pages 1-20 -o -       # print pages 1-20 to the terminal
pdf2md photo.jpg                        # photos of pages work too
```

Getting the best out of handwritten notes:

```bash
pdf2md notes.pdf --lang guj+eng                # the languages on the page
pdf2md notes.pdf --hint "class 10 chemistry"   # context helps with unclear words
pdf2md notes.pdf --tiles 2                     # small, dense writing: read in 2 strips
pdf2md notes.pdf --enhance                     # faint pencil or grey photocopies
pdf2md notes.pdf --ocr-pages 4,9               # force OCR on pages "auto" got wrong
pdf2md notes.pdf --mode ocr                    # read every page from its image
```

Then search the output for `[?]` and `[illegible]`: those are the only words
worth checking by eye.

Other options (`pdf2md --help` has them all):

| Option | What it does |
| --- | --- |
| `--mode digital` | never OCR; typed pages only (fastest) |
| `--page-markers` | put `<!-- page N -->` before each page |
| `--keep-headers` | keep running headers, footers and page numbers |
| `--no-images` | do not save images |
| `--password PW` | open an encrypted PDF |
| `--engine tesseract` / `vlm` / `none` | force a reader |
| `--workers N` | pages read at once (for servers that run requests in parallel) |
| `--no-cache` | do not reuse earlier OCR results |

Exit status: `0` done, `1` error, `3` some pages could not be transcribed
(for example, no OCR engine was available).

## Offline and private by design

- Typed PDFs are converted entirely inside the Python process.
- OCR goes only to a model server **on this computer or your local network**.
  Any other address is refused unless you pass `--allow-remote`.
- Nothing else ever connects anywhere. There is no telemetry.
- OCR results are cached (keyed on the exact page image and settings) in
  `~/.cache/pdf2md` (`%LOCALAPPDATA%\pdf2md\cache` on Windows). Re-running a
  long notebook costs nothing, and an interrupted run resumes where it
  stopped. Use `--no-cache`, or delete that folder, to opt out.

## From Python

```python
from pdf2md import convert, Options

result = convert("notes.pdf", "notes.md", Options(lang="hin+eng", tiles=2))
print(result.summary())      # 12 pages: 3 from text layer, 9 via vlm, 2 images
print(result.markdown[:500])
for warning in result.warnings:
    print(warning)
```

## Limits worth knowing

- **Handwriting accuracy depends on the model and the writing.** No OCR is
  perfect on handwriting; the output marks its doubts with `[?]`. Accuracy
  varies by script: try a few pages first (`--pages 1-3`), and compare
  models if one struggles with your language.
- Tables in typed PDFs are found when they have ruled lines; tables without
  borders come out as text lines. Scanned tables are fine with a vision
  model; Tesseract cannot rebuild tables.
- Maths in typed PDFs comes out as the Unicode characters the PDF contains,
  not LaTeX. Use `--ocr-pages` with a vision model for LaTeX.
- A hyphen at a line end is ambiguous ("trace-compilation" vs "trace-
  compilation"). pdf2md removes it unless the document itself spells the word
  with a hyphen elsewhere.
- Handwriting that a note app flattened into a typed PDF as filled shapes
  (not as ink annotations) cannot be told apart from drawings. Use
  `--ocr-pages` for those pages.
- Very unusual layouts (magazines, posters) may read in an odd order; a
  vision model with `--mode ocr` handles those better.

## Development

```bash
pip install -e ".[test]"
python -m pytest
```

The tests build their own PDFs and include a mock Ollama/OpenAI server, so they
run offline. Tesseract tests are skipped if it is not installed.

pdf2md depends on PyMuPDF, which is licensed under the AGPL. That is fine for
personal use; check the terms before shipping pdf2md inside a closed product.
