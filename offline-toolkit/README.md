# Offline Toolkit

A fully offline desktop app (Electron + React + TypeScript, with Python workers and bundled engines) with four modules:

1. **Image resizer and compressor**: exact pixels or physical size at any DPI, and a target file-size range.
2. **Universal document converter**: PDF, PDF/A-1b/2b/3b, DOCX/DOC, XLSX/XLS, PPTX/PPT, HTML, TXT, EPUB, PNG, JPEG and SVG in every direction, with a verification report for every job.
3. **Image to editable design**: OCR, layout analysis, inpainting and vectorising into a layered, editable document.
4. **Passport photo maker**: a 4-step wizard with face-guided sizing, background replacement and print sheets.

## Status

| Phase | State |
|---|---|
| 0: architecture, engines, conversion matrix | **Awaiting your confirmation** |
| 1: Module 1 | not started |
| 2: Module 2 | not started (route planner done) |
| 3: Module 3 | not started |
| 4: Module 4 | not started |
| 5: merge, installer, README, samples | not started |

## Phase 0 documents

- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md): architecture, process model, offline guarantee, module designs, folder structure, tests, packaging, known limits, **and the decisions I need from you (§0)**.
- [docs/ENGINES.md](docs/ENGINES.md): every bundled component with its license and GPL/AGPL flags.
- [docs/CONVERSION_MATRIX.md](docs/CONVERSION_MATRIX.md): every source × target pair in Module 2, with the engine chain for each fidelity mode and its potential losses. It is generated from `resources/defaults/conversion/*.json` by the real planner.

## Working with the Phase 0 code

Requires Python 3.11+.

```bash
cd offline-toolkit
python scripts/gen_matrix.py            # regenerate docs/CONVERSION_MATRIX.md + docs/conversion-matrix.json
python scripts/gen_matrix.py --check    # fail if they are stale
cd worker && python -m pytest -q        # planner tests (every pair routed, regressions, validation, matrix freshness)
```

To change a route, edit `resources/defaults/conversion/routes.json` (edges, costs, overrides) or `formats.json` (what each format can hold). Then regenerate the matrix; the tests check the result.

Full setup, build, packaging and troubleshooting instructions arrive in Phase 5.
