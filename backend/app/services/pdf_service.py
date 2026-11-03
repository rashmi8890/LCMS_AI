# import os
# from io import BytesIO

# import pymupdf
# from PIL import Image


# def _configure_tesseract():
#     import pytesseract

#     configured_path = os.getenv("TESSERACT_CMD")
#     if configured_path:
#         pytesseract.pytesseract.tesseract_cmd = configured_path
#     else:
#         # Common Windows installation location. If Tesseract is already on PATH,
#         # pytesseract will use it without this fallback.
#         windows_path = r"C:\Program Files\Tesseract-OCR\tesseract.exe"
#         if os.name == "nt" and os.path.exists(windows_path):
#             pytesseract.pytesseract.tesseract_cmd = windows_path

#     return pytesseract


# def _ocr_page(page):
#     """Render a PDF page and OCR it. Requires Tesseract + pytesseract + Pillow."""
#     try:
#         pytesseract = _configure_tesseract()
#         language = os.getenv("TESSERACT_LANG", "eng")
#         dpi = int(os.getenv("OCR_DPI", "300"))

#         pix = page.get_pixmap(
#             matrix=pymupdf.Matrix(dpi / 72, dpi / 72),
#             alpha=False,
#         )
#         image = Image.open(BytesIO(pix.tobytes("png")))
#         return pytesseract.image_to_string(image, lang=language).strip()
#     except ImportError as exc:
#         raise RuntimeError(
#             "OCR is required for this PDF, but Python OCR dependencies are missing. "
#             "Install them with: python -m pip install pytesseract Pillow"
#         ) from exc
#     except Exception as exc:
#         raise RuntimeError(
#             "OCR could not run. Install the Tesseract OCR application, ensure "
#             "tesseract.exe is on PATH (or set TESSERACT_CMD), and install the "
#             f"requested language data. Original error: {exc}"
#         ) from exc


# def extract_pdf(path: str):
#     """
#     Extract text page by page.

#     First tries the PDF's embedded text layer. If a page has little or no text,
#     it falls back to OCR. The returned shape remains compatible with the
#     existing documents router.
#     """
#     pages = []

#     with pymupdf.open(path) as doc:
#         for index, page in enumerate(doc):
#             native_text = (page.get_text("text") or "").strip()
#             text = native_text
#             ocr_used = False

#             # A page with only a few characters is commonly an image-only scan
#             # or has an unusable/incomplete text layer.
#             if len(native_text) < 40:
#                 ocr_text = _ocr_page(page)
#                 if len(ocr_text) > len(native_text):
#                     text = ocr_text
#                     ocr_used = True

#             pages.append({
#                 "page_number": index + 1,
#                 "text": text,
#                 "width": float(page.rect.width),
#                 "height": float(page.rect.height),
#                 "ocr_used": ocr_used,
#             })

#     return pages


# def split_text(text: str, max_chars: int = 1500, overlap: int = 180):
#     clean = " ".join((text or "").split())
#     chunks = []
#     start = 0

#     while start < len(clean):
#         end = min(start + max_chars, len(clean))
#         if end < len(clean):
#             boundary = clean.rfind(" ", start, end)
#             if boundary > start + max_chars // 2:
#                 end = boundary

#         chunk = clean[start:end].strip()
#         if chunk:
#             chunks.append(chunk)

#         if end >= len(clean):
#             break
#         start = max(end - overlap, start + 1)

#     return chunks


# def find_highlight_rects(pdf_path: str, page_number: int, quote: str):
#     """
#     Locate source quotes in the PDF's original text layer.

#     OCR-extracted text does not automatically create a text layer in the PDF,
#     so OCR-only pages may need a future OCR-coordinate/highlight implementation.
#     """
#     with pymupdf.open(pdf_path) as doc:
#         page = doc[page_number - 1]
#         rects = page.search_for(quote, quads=False)

#         if not rects:
#             words = [word for word in quote.split() if len(word) > 3]
#             if words:
#                 rects = page.search_for(" ".join(words[:8]), quads=False)

#         return [
#             {
#                 "x0": float(rect.x0),
#                 "y0": float(rect.y0),
#                 "x1": float(rect.x1),
#                 "y1": float(rect.y1),
#                 "page_width": float(page.rect.width),
#                 "page_height": float(page.rect.height),
#             }
#             for rect in rects
#         ]

import os
import re
import logging
from io import BytesIO
from difflib import SequenceMatcher

import pymupdf
from PIL import Image


logger = logging.getLogger(__name__)


def _configure_tesseract():
    import pytesseract

    configured_path = os.getenv("TESSERACT_CMD")

    if configured_path:
        pytesseract.pytesseract.tesseract_cmd = configured_path
    else:
        windows_path = r"C:\Program Files\Tesseract-OCR\tesseract.exe"

        if os.name == "nt" and os.path.exists(windows_path):
            pytesseract.pytesseract.tesseract_cmd = windows_path

    return pytesseract


def _render_page(page, dpi=300):
    """Render a PDF page for OCR."""
    scale = dpi / 72

    pix = page.get_pixmap(
        matrix=pymupdf.Matrix(scale, scale),
        alpha=False,
    )

    image = Image.open(BytesIO(pix.tobytes("png")))

    return image, scale


def _ocr_page(page):
    """Extract OCR text. Preserves the existing extract_pdf interface."""
    try:
        pytesseract = _configure_tesseract()

        language = os.getenv("TESSERACT_LANG", "eng")
        dpi = int(os.getenv("OCR_DPI", "300"))

        image, _ = _render_page(page, dpi)

        return pytesseract.image_to_string(
            image,
            lang=language,
        ).strip()

    except ImportError as exc:
        raise RuntimeError(
            "OCR dependencies are missing. Install with: "
            "python -m pip install pytesseract Pillow"
        ) from exc

    except Exception as exc:
        raise RuntimeError(
            "OCR failed. Check Tesseract installation, language data "
            f"and TESSERACT_CMD. Original error: {exc}"
        ) from exc


def _normalise_token(value):
    """Normalize a word for matching without changing source text."""
    return re.sub(r"[^a-z0-9]", "", str(value).lower())


def _quote_tokens(quote):
    return [
        token
        for token in (_normalise_token(w) for w in (quote or "").split())
        if token
    ]


def _group_word_rectangles(words, page_width, page_height):
    """
    Convert matched word boxes into one rectangle per visual text line.

    Each input item:
        (x0, y0, x1, y1, text, line_key)
    """
    if not words:
        return []

    grouped = {}

    for word in words:
        x0, y0, x1, y1 = word[:4]
        line_key = word[5]

        grouped.setdefault(line_key, []).append(
            (x0, y0, x1, y1)
        )

    results = []

    for line_words in grouped.values():
        results.append({
            "x0": float(min(w[0] for w in line_words)),
            "y0": float(min(w[1] for w in line_words)),
            "x1": float(max(w[2] for w in line_words)),
            "y1": float(max(w[3] for w in line_words)),
            "page_width": float(page_width),
            "page_height": float(page_height),
        })

    return results

def _find_word_sequence(word_entries, quote, page):
    """
    Match a quotation against extracted word boxes.

    Exact normalized token matching is preferred.
    Fuzzy matching is used only when an exact match is unavailable.
    """
    quote_words = _quote_tokens(quote)

    if len(quote_words) < 3:
        return []

    source_words = [
        _normalise_token(entry[4])
        for entry in word_entries
    ]

    if not source_words:
        return []

    target_length = len(quote_words)

    # First: exact contiguous token sequence.
    for start in range(len(source_words) - target_length + 1):
        candidate = source_words[start:start + target_length]

        if candidate == quote_words:
            matched = word_entries[start:start + target_length]

            return _group_word_rectangles(
                matched,
                page.rect.width,
                page.rect.height,
            )

    # Second: cautious fuzzy matching.
    # Do not accept short or weak matches as complete quotations.
    if target_length < 5:
        return []

    best_score = 0
    best_start = None
    best_length = None

    minimum_length = max(3, target_length - 2)
    maximum_length = min(
        len(source_words),
        target_length + 2,
    )

    for window_length in range(minimum_length, maximum_length + 1):
        for start in range(
            len(source_words) - window_length + 1
        ):
            candidate = source_words[
                start:start + window_length
            ]

            score = SequenceMatcher(
                None,
                quote_words,
                candidate,
                autojunk=False,
            ).ratio()

            if score > best_score:
                best_score = score
                best_start = start
                best_length = window_length

    # Conservative threshold to avoid highlighting unrelated text.
    if (
        best_start is None
        or best_score < 0.92
        or best_length is None
    ):
        return []

    matched = word_entries[
        best_start:best_start + best_length
    ]

    logger.info(
        "Fuzzy PDF quotation match: score=%.3f, "
        "quote_words=%s, matched_words=%s",
        best_score,
        target_length,
        best_length,
    )

    return _group_word_rectangles(
        matched,
        page.rect.width,
        page.rect.height,
    )


def _native_word_entries(page):
    """Read native PDF words with their coordinates."""
    entries = []

    for word in page.get_text("words"):
        # PyMuPDF word tuple:
        # x0, y0, x1, y1, word, block_no, line_no, word_no
        x0, y0, x1, y1, text = word[:5]
        block_no = word[5]
        line_no = word[6]

        entries.append((
            float(x0),
            float(y0),
            float(x1),
            float(y1),
            str(text),
            (block_no, line_no),
        ))

    return entries


def _ocr_word_entries(page):
    """OCR the page and return word-level PDF coordinates."""
    try:
        pytesseract = _configure_tesseract()

        language = os.getenv("TESSERACT_LANG", "eng")
        dpi = int(os.getenv("OCR_DPI", "300"))

        image, scale = _render_page(page, dpi)

        data = pytesseract.image_to_data(
            image,
            lang=language,
            output_type=pytesseract.Output.DICT,
            config="--psm 3",
        )

        entries = []

        count = len(data["text"])

        for index in range(count):
            text = str(data["text"][index]).strip()

            if not text:
                continue

            try:
                confidence = float(data["conf"][index])
            except (TypeError, ValueError):
                continue

            # Ignore extremely unreliable OCR words.
            if confidence < 35:
                continue

            left = float(data["left"][index])
            top = float(data["top"][index])
            width = float(data["width"][index])
            height = float(data["height"][index])

            # Convert rendered image pixels back to PDF coordinates.
            x0 = page.rect.x0 + left / scale
            y0 = page.rect.y0 + top / scale
            x1 = page.rect.x0 + (left + width) / scale
            y1 = page.rect.y0 + (top + height) / scale

            line_key = (
                data["block_num"][index],
                data["par_num"][index],
                data["line_num"][index],
            )

            entries.append((
                x0,
                y0,
                x1,
                y1,
                text,
                line_key,
            ))

        return entries

    except ImportError as exc:
        raise RuntimeError(
            "OCR dependencies are missing. Install pytesseract and Pillow."
        ) from exc


def extract_pdf(path: str):
    """
    Extract text page by page.

    Existing return structure is preserved:
        page_number, text, width, height, ocr_used
    """
    pages = []

    with pymupdf.open(path) as doc:
        for index, page in enumerate(doc):
            native_text = (
                page.get_text("text") or ""
            ).strip()

            text = native_text
            ocr_used = False

            if len(native_text) < 40:
                try:
                    ocr_text = _ocr_page(page)

                    if len(ocr_text) > len(native_text):
                        text = ocr_text
                        ocr_used = True

                except Exception:
                    logger.exception(
                        "OCR extraction failed for page %s of %s",
                        index + 1,
                        path,
                    )

            pages.append({
                "page_number": index + 1,
                "text": text,
                "width": float(page.rect.width),
                "height": float(page.rect.height),
                "ocr_used": ocr_used,
            })

    return pages


def split_text(text: str, max_chars: int = 1500, overlap: int = 180):
    """Existing chunking behaviour preserved."""
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


def find_highlight_rects(
    pdf_path: str,
    page_number: int,
    quote: str,
):
    """
    Locate quotation coordinates using:

    1. Native exact PDF search.
    2. Native normalized word matching.
    3. OCR word-level coordinate matching.

    Returns the same coordinate dictionary format expected by
    the existing analysis.py citation persistence logic.
    """
    if not quote or not quote.strip():
        return []

    try:
        with pymupdf.open(pdf_path) as doc:
            if page_number < 1 or page_number > len(doc):
                logger.warning(
                    "Invalid highlight page: %s in %s",
                    page_number,
                    pdf_path,
                )
                return []

            page = doc[page_number - 1]

            # 1. Native exact search.
            rects = page.search_for(
                quote.strip(),
                quads=False,
            )

            if rects:
                logger.info(
                    "Native exact highlight match: file=%s page=%s "
                    "rectangles=%s",
                    os.path.basename(pdf_path),
                    page_number,
                    len(rects),
                )

                return [
                    {
                        "x0": float(r.x0),
                        "y0": float(r.y0),
                        "x1": float(r.x1),
                        "y1": float(r.y1),
                        "page_width": float(page.rect.width),
                        "page_height": float(page.rect.height),
                    }
                    for r in rects
                ]

            # 2. Native word-level normalized matching.
            native_words = _native_word_entries(page)

            rects = _find_word_sequence(
                native_words,
                quote,
                page,
            )

            if rects:
                logger.info(
                    "Native normalized highlight match: file=%s "
                    "page=%s rectangles=%s",
                    os.path.basename(pdf_path),
                    page_number,
                    len(rects),
                )

                return rects

            # 3. OCR word-level matching.
            logger.info(
                "Native matching failed; trying OCR: file=%s page=%s",
                os.path.basename(pdf_path),
                page_number,
            )

            ocr_words = _ocr_word_entries(page)

            rects = _find_word_sequence(
                ocr_words,
                quote,
                page,
            )

            if rects:
                logger.info(
                    "OCR highlight match: file=%s page=%s "
                    "rectangles=%s",
                    os.path.basename(pdf_path),
                    page_number,
                    len(rects),
                )

                return rects

            logger.warning(
                "No highlight match found: file=%s page=%s quote=%r",
                os.path.basename(pdf_path),
                page_number,
                quote[:200],
            )

            return []

    except Exception:
        logger.exception(
            "Highlight generation failed: file=%s page=%s",
            pdf_path,
            page_number,
        )

        return []
