import time

import pytest

tk = pytest.importorskip("tkinter")


@pytest.fixture
def app():
    try:
        root = tk.Tk()
    except tk.TclError:
        pytest.skip("no display for Tk")
    root.withdraw()
    from pdf2md.gui import App

    a = App(root)
    yield a
    root.destroy()


def pump(app, until, timeout=60):
    end = time.time() + timeout
    while time.time() < end:
        app.root.update()
        if until():
            return
        time.sleep(0.02)
    raise AssertionError("timed out")


def test_converts_the_chosen_pdfs(app, digital_pdf, tmp_path):
    app.add_paths([digital_pdf])
    app.add_paths([digital_pdf])  # added once only
    assert app.files == [digital_pdf]
    app.out_dir.set(str(tmp_path))
    app.mode.set("digital")
    app.start()
    pump(app, lambda: not app.busy)
    out = tmp_path / "digital.md"
    assert app.outputs == [out]
    assert out.read_text(encoding="utf-8").startswith("# Quarterly Notes")
    assert str(app.open_btn["state"]) == "normal"
    assert "Finished: 1 of 1 converted." in app.log_text.get("1.0", "end")


def test_a_broken_file_is_reported_and_the_rest_continue(app, digital_pdf, tmp_path):
    bad = tmp_path / "broken.pdf"
    bad.write_bytes(b"not a pdf at all")
    app.add_paths([bad, digital_pdf])
    app.out_dir.set(str(tmp_path / "out"))
    app.mode.set("digital")
    app.start()
    pump(app, lambda: not app.busy)
    log = app.log_text.get("1.0", "end")
    assert "FAILED broken.pdf" in log
    assert (tmp_path / "out" / "digital.md").exists()


def test_bad_option_is_shown_not_crashed(app, digital_pdf, monkeypatch):
    shown = []
    monkeypatch.setattr("pdf2md.gui.messagebox.showerror", lambda title, msg: shown.append(msg))
    app.add_paths([digital_pdf])
    app.tiles.set(9)
    app.start()
    assert shown and "tiles" in shown[0]
    assert not app.busy
