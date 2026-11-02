# import fitz

# def extract_pdf(path: str):
#     doc = fitz.open(path)
#     pages = []
#     for i, page in enumerate(doc):
#         pages.append({"page_number": i + 1, "text": page.get_text("text") or "",
#                       "width": float(page.rect.width), "height": float(page.rect.height)})
#     doc.close()
#     return pages

# def split_text(text: str, max_chars: int = 1500, overlap: int = 180):
#     clean = " ".join((text or "").split())
#     chunks, start = [], 0
#     while start < len(clean):
#         end = min(start + max_chars, len(clean))
#         if end < len(clean):
#             boundary = clean.rfind(" ", start, end)
#             if boundary > start + max_chars // 2: end = boundary
#         chunks.append(clean[start:end].strip())
#         if end >= len(clean): break
#         start = max(end - overlap, start + 1)
#     return [c for c in chunks if c]

# def find_highlight_rects(pdf_path: str, page_number: int, quote: str):
#     # PDF text search returns all matching rectangles; no LLM-generated coordinates are trusted.
#     doc = fitz.open(pdf_path)
#     try:
#         page = doc[page_number - 1]
#         rects = page.search_for(quote, quads=False)
#         if not rects:
#             # Retry normalized whitespace fragments; exact phrase may wrap differently.
#             words = [w for w in quote.split() if len(w) > 3]
#             if words:
#                 rects = page.search_for(" ".join(words[:8]), quads=False)
#         return [{"x0": float(r.x0), "y0": float(r.y0), "x1": float(r.x1), "y1": float(r.y1),
#                  "page_width": float(page.rect.width), "page_height": float(page.rect.height)}
#                 for r in rects]
#     finally:
#         doc.close()

import os
from io import BytesIO

import pymupdf
from PIL import Image


def _configure_tesseract():
    import pytesseract

    configured_path = os.getenv("TESSERACT_CMD")
    if configured_path:
        pytesseract.pytesseract.tesseract_cmd = configured_path
    else:
        # Common Windows installation location. If Tesseract is already on PATH,
        # pytesseract will use it without this fallback.
        windows_path = r"C:\Program Files\Tesseract-OCR\tesseract.exe"
        if os.name == "nt" and os.path.exists(windows_path):
            pytesseract.pytesseract.tesseract_cmd = windows_path

    return pytesseract


def _ocr_page(page):
    """Render a PDF page and OCR it. Requires Tesseract + pytesseract + Pillow."""
    try:
        pytesseract = _configure_tesseract()
        language = os.getenv("TESSERACT_LANG", "eng")
        dpi = int(os.getenv("OCR_DPI", "300"))

        pix = page.get_pixmap(
            matrix=pymupdf.Matrix(dpi / 72, dpi / 72),
            alpha=False,
        )
        image = Image.open(BytesIO(pix.tobytes("png")))
        return pytesseract.image_to_string(image, lang=language).strip()
    except ImportError as exc:
        raise RuntimeError(
            "OCR is required for this PDF, but Python OCR dependencies are missing. "
            "Install them with: python -m pip install pytesseract Pillow"
        ) from exc
    except Exception as exc:
        raise RuntimeError(
            "OCR could not run. Install the Tesseract OCR application, ensure "
            "tesseract.exe is on PATH (or set TESSERACT_CMD), and install the "
            f"requested language data. Original error: {exc}"
        ) from exc


def extract_pdf(path: str):
    """
    Extract text page by page.

    First tries the PDF's embedded text layer. If a page has little or no text,
    it falls back to OCR. The returned shape remains compatible with the
    existing documents router.
    """
    pages = []

    with pymupdf.open(path) as doc:
        for index, page in enumerate(doc):
            native_text = (page.get_text("text") or "").strip()
            text = native_text
            ocr_used = False

            # A page with only a few characters is commonly an image-only scan
            # or has an unusable/incomplete text layer.
            if len(native_text) < 40:
                ocr_text = _ocr_page(page)
                if len(ocr_text) > len(native_text):
                    text = ocr_text
                    ocr_used = True

            pages.append({
                "page_number": index + 1,
                "text": text,
                "width": float(page.rect.width),
                "height": float(page.rect.height),
                "ocr_used": ocr_used,
            })

    return pages


def split_text(text: str, max_chars: int = 1500, overlap: int = 180):
    clean = " ".join((text or "").split())
    chunks = []
    start = 0

    while start < len(clean):
        end = min(start + max_chars, len(clean))
        if end < len(clean):
            boundary = clean.rfind(" ", start, end)
            if boundary > start + max_chars // 2:
                end = boundary

        chunk = clean[start:end].strip()
        if chunk:
            chunks.append(chunk)

        if end >= len(clean):
            break
        start = max(end - overlap, start + 1)

    return chunks


def find_highlight_rects(pdf_path: str, page_number: int, quote: str):
    """
    Locate source quotes in the PDF's original text layer.

    OCR-extracted text does not automatically create a text layer in the PDF,
    so OCR-only pages may need a future OCR-coordinate/highlight implementation.
    """
    with pymupdf.open(pdf_path) as doc:
        page = doc[page_number - 1]
        rects = page.search_for(quote, quads=False)

        if not rects:
            words = [word for word in quote.split() if len(word) > 3]
            if words:
                rects = page.search_for(" ".join(words[:8]), quads=False)

        return [
            {
                "x0": float(rect.x0),
                "y0": float(rect.y0),
                "x1": float(rect.x1),
                "y1": float(rect.y1),
                "page_width": float(page.rect.width),
                "page_height": float(page.rect.height),
            }
            for rect in rects
        ]

