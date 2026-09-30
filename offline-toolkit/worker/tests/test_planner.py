import copy
import json
import subprocess
import sys
from pathlib import Path

import pytest

from otk_worker.converter.planner import MODES, Catalog, CatalogError, NoRouteError

ROOT = Path(__file__).resolve().parents[2]
CONVERSION_DIR = ROOT / "resources" / "defaults" / "conversion"


@pytest.fixture(scope="module")
def cat() -> Catalog:
    return Catalog.from_dir(CONVERSION_DIR)


def raw_docs():
    with open(CONVERSION_DIR / "formats.json", encoding="utf-8") as fh:
        formats = json.load(fh)
    with open(CONVERSION_DIR / "routes.json", encoding="utf-8") as fh:
        routes = json.load(fh)
    return formats, routes


def all_requests(cat):
    for s in cat.sources():
        for t in cat.targets():
            if s != t:
                for m in MODES:
                    yield s, t, m


def test_requested_formats_are_both_source_and_target(cat):
    required = {"pdf", "pdfa1b", "pdfa2b", "pdfa3b", "docx", "xlsx", "pptx", "html", "txt", "epub", "png", "jpeg", "svg"}
    assert required <= set(cat.sources())
    assert required <= set(cat.targets())
    assert {"doc", "xls", "ppt"} <= set(cat.sources())


def test_every_pair_and_mode_has_a_valid_route(cat):
    count = 0
    for s, t, m in all_requests(cat):
        route = cat.plan(s, t, m)
        count += 1
        chain = route.chain
        assert chain[0] == s and chain[-1] == t
        assert len(set(chain)) == len(chain), f"{s}->{t} revisits a format: {chain}"
        assert route.mode == cat.effective_mode(t, m)
        for i, step in enumerate(route.steps):
            assert route.mode in step.costs, f"{step.id} used outside its modes"
            if step.final_only:
                assert i == len(route.steps) - 1, f"{step.id} is final-only but not last"
        for mid in chain[1:-1]:
            fmt = cat.formats[mid]
            assert fmt.get("transit") or mid == cat.formats[s].get("normalizeTo"), f"{s}->{t} passes through {mid}"
    assert count == 17 * 13 * 2 - 13 * 2  # 17 sources, 13 targets, both modes, minus same-format pairs


def test_single_mode_targets_ignore_editable(cat):
    for t in ("pdf", "pdfa1b", "png", "jpeg", "txt"):
        assert cat.plan("docx" if t != "docx" else "pdf", t, "editable").mode == "exact"


@pytest.mark.parametrize("source,target,mode,edges", [
    ("pdf", "docx", "editable", ["pdf2docx"]),
    ("pdf", "docx", "exact", ["lo_pdf_import_docx"]),
    ("docx", "pdfa2b", "exact", ["lo_to_pdfa"]),
    ("pdfa1b", "pdfa2b", "exact", ["pdfa_upgrade"]),
    ("pdfa3b", "pdfa1b", "exact", ["pdfa_read", "gs_pdfa"]),
    ("png", "pdf", "exact", ["img2pdf", "scanned_as_pdf"]),
    ("pdf_scanned", "docx", "editable", ["ocr", "ocr_docx_editable"]),
    ("pdf_scanned", "jpeg", "exact", ["scanned_extract"]),
    ("doc", "txt", "exact", ["legacy_to_ooxml", "pandoc_plain"]),
    ("ppt", "html", "exact", ["legacy_to_ooxml", "pptx_html_exact"]),
    ("epub", "xlsx", "editable", ["epub_html_join", "html_xlsx"]),
    ("svg", "jpeg", "exact", ["resvg", "raster_convert"]),
    ("txt", "pdf", "exact", ["txt_html", "chromium_pdf"]),
])
def test_expected_routes(cat, source, target, mode, edges):
    assert [s.id for s in cat.plan(source, target, mode).steps] == edges


@pytest.mark.parametrize("source", ["html", "txt", "epub", "svg"])
def test_pdfa_goes_through_pdf_not_office_detours(cat, source):
    # Regression: Office formats used to be transit nodes, giving HTML -> XLSX -> PDF/A.
    for level in ("pdfa1b", "pdfa2b", "pdfa3b"):
        chain = cat.plan(source, level, "exact").chain
        assert chain[-2] == "pdf", chain


def test_text_is_never_routed_through_txt(cat):
    for s, t, m in all_requests(cat):
        if s != "txt" and t != "txt":
            assert "txt" not in cat.plan(s, t, m).chain


def test_override_is_used_and_flagged(cat):
    route = cat.plan("svg", "xlsx", "editable")
    assert route.chain == ["svg", "pdf", "docmodel", "xlsx"]
    assert route.override_reason


def test_losses(cat):
    def lost(s, t, m="exact"):
        return {l.feature: l.level for l in cat.potential_losses(cat.plan(s, t, m))}

    assert lost("docx", "txt")["images"] == "lost"
    assert lost("xlsx", "pdf")["formulas"] == "lost"
    assert lost("pdfa3b", "pdfa1b")["attachments"] == "lost"
    assert "text" not in lost("txt", "docx")
    assert lost("xlsx", "html", "editable")["formulas"] == "reduced"  # kept in data- attributes, not live
    assert "text" not in lost("png", "txt")  # recoverable through OCR
    step = cat.step_losses(cat.plan("pdf", "pdfa1b", "exact"))
    assert "Transparency flattened" in step
    assert any("Fonts not embedded" in t for t in step)
    # Conditional step loss: an image has no fonts to substitute.
    assert not any("Fonts not embedded" in t for t in cat.step_losses(cat.plan("png", "pdfa2b", "exact")))


def test_bad_requests(cat):
    with pytest.raises(NoRouteError):
        cat.plan("docx", "docx", "exact")
    with pytest.raises(NoRouteError):
        cat.plan("docx", "doc", "exact")  # legacy formats are input-only
    with pytest.raises(NoRouteError):
        cat.plan("docmodel", "pdf", "exact")  # internal
    with pytest.raises(ValueError):
        cat.plan("docx", "pdf", "fast")


@pytest.mark.parametrize("mutate,message", [
    (lambda f, r: r["edges"].append({"id": "x", "from": ["docx"], "to": ["nope"], "engine": "python", "modes": {"exact": 1}}), "unknown format"),
    (lambda f, r: r["edges"].append({"id": "x", "from": ["docx"], "to": ["pdf"], "engine": "nope", "modes": {"exact": 1}}), "unknown engine"),
    (lambda f, r: r["edges"].append({"id": "x", "from": ["docx"], "to": ["pdf"], "engine": "python", "modes": {"exact": -1}}), "negative"),
    (lambda f, r: r["edges"].append({"id": "x", "from": ["docx"], "to": ["docx"], "engine": "python", "modes": {"exact": 1}}), "itself"),
    (lambda f, r: r["edges"].append(copy.deepcopy(r["edges"][0])), "duplicated"),
    (lambda f, r: f["formats"]["pdf"]["caps"].pop("text"), "caps missing"),
    (lambda f, r: f["formats"]["pdf"]["caps"].update(text="most"), "bad level"),
    (lambda f, r: r["edges"].append({"id": "x", "from": ["docx"], "to": ["pdf"], "engine": "python", "modes": {"exact": 1},
                                     "losses": [{"text": "t", "ifSourceHas": "nope"}]}), "unknown feature"),
])
def test_catalog_validation(mutate, message):
    formats, routes = raw_docs()
    mutate(formats, routes)
    with pytest.raises(CatalogError, match=message):
        Catalog(formats, routes)


def test_generated_matrix_is_up_to_date():
    result = subprocess.run([sys.executable, str(ROOT / "scripts" / "gen_matrix.py"), "--check"],
                            capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
