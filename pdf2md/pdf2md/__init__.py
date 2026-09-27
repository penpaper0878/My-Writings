"""pdf2md — convert any PDF, including scanned and handwritten notes, to Markdown offline.

Digital pages are read straight from the PDF's text layer, so nothing is guessed.
Scanned, photographed and handwritten pages are transcribed by a vision model
running on your own machine (Ollama, llama.cpp, LM Studio …) or by Tesseract.

    from pdf2md import convert, Options
    result = convert("notes.pdf", options=Options(engine="vlm"))
    print(result.markdown)
"""

__version__ = "1.0.0"
__all__ = ["Options", "ConversionResult", "convert", "__version__"]


def __getattr__(name):
    if name == "Options":
        from .options import Options

        return Options
    if name in ("convert", "ConversionResult"):
        from . import converter

        return getattr(converter, name)
    raise AttributeError(name)
