"""A small window for pdf2md: pick PDFs, press Convert.

Runs the same converter as the command line, in a background thread, and
shows its progress. Built on tkinter, which ships with Python on Windows and
macOS, so the .exe needs nothing else.
"""

from __future__ import annotations

import os
import queue
import subprocess
import sys
import threading
from pathlib import Path

import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from . import __version__
from .options import DEFAULT_MODEL, Options

PDF_TYPES = [
    ("PDFs and images", "*.pdf *.png *.jpg *.jpeg *.tif *.tiff *.bmp *.webp"),
    ("PDF", "*.pdf"),
    ("All files", "*.*"),
]
LANGUAGES = ["", "eng", "hin+eng", "guj+eng", "guj+hin+eng", "mar+eng", "hin", "guj"]


def open_path(path: Path) -> None:
    """Show a file or folder in the system's file manager."""
    try:
        if sys.platform == "win32":
            os.startfile(str(path))  # noqa: S606 - opening the user's own output
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(path)])
        else:
            subprocess.Popen(["xdg-open", str(path)])
    except OSError:
        pass


class App:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.files: list = []
        self.outputs: list = []
        self.events: "queue.Queue" = queue.Queue()
        self.busy = False

        root.title(f"pdf2md {__version__} - PDF to Markdown, offline")
        root.minsize(640, 560)
        pad = {"padx": 8, "pady": 4}

        # ---- files
        box = ttk.LabelFrame(root, text="1. PDFs to convert")
        box.pack(fill="both", expand=False, **pad)
        self.listbox = tk.Listbox(box, height=6, selectmode="extended")
        self.listbox.pack(side="left", fill="both", expand=True, padx=(8, 0), pady=8)
        buttons = ttk.Frame(box)
        buttons.pack(side="right", fill="y", padx=8, pady=8)
        ttk.Button(buttons, text="Add PDFs...", command=self.add_files).pack(fill="x")
        ttk.Button(buttons, text="Add folder...", command=self.add_folder).pack(fill="x", pady=4)
        ttk.Button(buttons, text="Remove", command=self.remove_selected).pack(fill="x")

        # ---- output
        box = ttk.LabelFrame(root, text="2. Where to save")
        box.pack(fill="x", **pad)
        self.out_dir = tk.StringVar(value="")
        ttk.Label(box, text="Folder:").grid(row=0, column=0, sticky="w", padx=8, pady=6)
        ttk.Entry(box, textvariable=self.out_dir).grid(row=0, column=1, sticky="ew", pady=6)
        ttk.Button(box, text="Choose...", command=self.choose_out).grid(row=0, column=2, padx=8)
        ttk.Label(box, text="(empty = next to each PDF)", foreground="gray").grid(
            row=1, column=1, sticky="w", pady=(0, 6)
        )
        box.columnconfigure(1, weight=1)

        # ---- options
        box = ttk.LabelFrame(root, text="3. Options")
        box.pack(fill="x", **pad)
        self.lang = tk.StringVar(value="")
        self.hint = tk.StringVar(value="")
        self.pages = tk.StringVar(value="")
        self.mode = tk.StringVar(value="auto")
        self.tiles = tk.IntVar(value=1)
        self.enhance = tk.BooleanVar(value=False)
        self.markers = tk.BooleanVar(value=False)
        self.model = tk.StringVar(value=os.environ.get("PDF2MD_MODEL", DEFAULT_MODEL))

        def row(r, label, widget, note=""):
            ttk.Label(box, text=label).grid(row=r, column=0, sticky="w", padx=8, pady=3)
            widget.grid(row=r, column=1, sticky="ew", pady=3)
            if note:
                ttk.Label(box, text=note, foreground="gray").grid(row=r, column=2, sticky="w", padx=8)

        row(0, "Languages:", ttk.Combobox(box, textvariable=self.lang, values=LANGUAGES), "e.g. guj+eng; empty = auto")
        row(1, "About the notes:", ttk.Entry(box, textvariable=self.hint), 'e.g. "class 10 chemistry"')
        row(2, "Pages:", ttk.Entry(box, textvariable=self.pages), "e.g. 1-5,8; empty = all")
        row(
            3, "Read pages:",
            ttk.Combobox(box, textvariable=self.mode, values=["auto", "digital", "ocr"], state="readonly"),
            "auto = best per page",
        )
        row(4, "Strips per page:", ttk.Spinbox(box, from_=1, to=4, textvariable=self.tiles, width=5),
            "2 helps small, dense handwriting")
        row(5, "Vision model:", ttk.Entry(box, textvariable=self.model), "an Ollama model")
        checks = ttk.Frame(box)
        checks.grid(row=6, column=1, sticky="w", pady=3)
        ttk.Checkbutton(checks, text="Boost faint pencil", variable=self.enhance).pack(side="left")
        ttk.Checkbutton(checks, text="Page markers", variable=self.markers).pack(side="left", padx=12)
        box.columnconfigure(1, weight=1)

        # ---- actions
        bar = ttk.Frame(root)
        bar.pack(fill="x", **pad)
        self.convert_btn = ttk.Button(bar, text="Convert", command=self.start)
        self.convert_btn.pack(side="left")
        ttk.Button(bar, text="Check setup", command=self.check).pack(side="left", padx=8)
        self.open_btn = ttk.Button(bar, text="Open result", command=self.open_result, state="disabled")
        self.open_btn.pack(side="left")
        self.progress = ttk.Progressbar(bar, mode="determinate")
        self.progress.pack(side="left", fill="x", expand=True, padx=(12, 0))

        # ---- log
        box = ttk.LabelFrame(root, text="Progress")
        box.pack(fill="both", expand=True, **pad)
        self.log_text = tk.Text(box, height=10, wrap="word", state="disabled")
        scroll = ttk.Scrollbar(box, command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=scroll.set)
        self.log_text.pack(side="left", fill="both", expand=True, padx=(8, 0), pady=8)
        scroll.pack(side="right", fill="y", pady=8, padx=(0, 8))

        self.log("Add PDFs (typed, scanned or handwritten) and press Convert.")
        self.log("Everything stays on this computer. Press 'Check setup' to see what can be read.")
        root.after(100, self.poll)

    # ------------------------------------------------------------ files
    def add_paths(self, paths) -> None:
        for p in paths:
            p = Path(p)
            if p not in self.files:
                self.files.append(p)
                self.listbox.insert("end", str(p))

    def add_files(self) -> None:
        self.add_paths(filedialog.askopenfilenames(title="Choose PDFs", filetypes=PDF_TYPES))

    def add_folder(self) -> None:
        folder = filedialog.askdirectory(title="Choose a folder of PDFs")
        if folder:
            found = sorted(Path(folder).rglob("*.pdf"))
            self.add_paths(found)
            self.log(f"Added {len(found)} PDF(s) from {folder}")

    def remove_selected(self) -> None:
        for i in reversed(self.listbox.curselection()):
            self.listbox.delete(i)
            del self.files[i]

    def choose_out(self) -> None:
        folder = filedialog.askdirectory(title="Save Markdown files in")
        if folder:
            self.out_dir.set(folder)

    # ------------------------------------------------------------ helpers
    def log(self, msg: str) -> None:
        self.log_text.configure(state="normal")
        self.log_text.insert("end", msg + "\n")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def options(self) -> Options:
        opts = Options(
            mode=self.mode.get(),
            pages=self.pages.get().strip() or None,
            lang=self.lang.get().strip() or None,
            hint=self.hint.get().strip() or None,
            tiles=int(self.tiles.get()),
            enhance=self.enhance.get(),
            page_markers=self.markers.get(),
            model=self.model.get().strip() or DEFAULT_MODEL,
        )
        opts.validate()
        return opts

    def output_for(self, src: Path) -> Path:
        folder = self.out_dir.get().strip()
        return (Path(folder) if folder else src.parent) / (src.stem + ".md")

    def set_busy(self, busy: bool) -> None:
        self.busy = busy
        self.convert_btn.configure(state="disabled" if busy else "normal")

    # ------------------------------------------------------------ work
    def start(self) -> None:
        if self.busy:
            return
        if not self.files:
            messagebox.showinfo("pdf2md", "Add at least one PDF first.")
            return
        try:
            opts = self.options()
        except ValueError as e:
            messagebox.showerror("pdf2md", str(e))
            return
        self.outputs = []
        self.open_btn.configure(state="disabled")
        self.progress.configure(maximum=len(self.files), value=0)
        self.set_busy(True)
        jobs = [(f, self.output_for(f)) for f in self.files]
        threading.Thread(target=self.work, args=(jobs, opts), daemon=True).start()

    def work(self, jobs, opts: Options) -> None:
        from .converter import convert

        say = lambda m: self.events.put(("log", m))  # noqa: E731
        for i, (src, out) in enumerate(jobs, 1):
            say(f"== {src.name}")
            try:
                result = convert(src, out, opts, log=say)
                for w in result.warnings:
                    say(f"warning: {w}")
                say(f"Saved {out}  ({result.summary()}, {result.seconds:.0f}s)")
                self.events.put(("done_file", out))
            except Exception as e:  # show every failure, keep going with the rest
                say(f"FAILED {src.name}: {e}")
            self.events.put(("progress", i))
        self.events.put(("finished", None))

    def check(self) -> None:
        try:
            opts = self.options()
        except ValueError as e:
            messagebox.showerror("pdf2md", str(e))
            return

        def run():
            import contextlib
            import io

            from .cli import check

            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                check(opts)
            for line in buf.getvalue().splitlines():
                self.events.put(("log", line))

        self.log("Checking setup...")
        threading.Thread(target=run, daemon=True).start()

    def poll(self) -> None:
        try:
            while True:
                kind, value = self.events.get_nowait()
                if kind == "log":
                    self.log(value)
                elif kind == "progress":
                    self.progress.configure(value=value)
                elif kind == "done_file":
                    self.outputs.append(value)
                elif kind == "finished":
                    self.set_busy(False)
                    if self.outputs:
                        self.open_btn.configure(state="normal")
                    self.log(f"Finished: {len(self.outputs)} of {len(self.files)} converted.")
        except queue.Empty:
            pass
        self.root.after(100, self.poll)

    def open_result(self) -> None:
        if len(self.outputs) == 1:
            open_path(self.outputs[0])
        elif self.outputs:
            open_path(self.outputs[0].parent)


def main() -> int:
    root = tk.Tk()
    App(root)
    root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
