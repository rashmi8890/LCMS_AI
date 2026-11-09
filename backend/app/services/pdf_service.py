# import os
# import re
# import logging
# from io import BytesIO
# from difflib import SequenceMatcher
# import pymupdf
# from PIL import Image

# logger = logging.getLogger(__name__)

# def _configure_tesseract():
#     import pytesseract
#     configured_path = os.getenv("TESSERACT_CMD")

#     if configured_path:
#         pytesseract.pytesseract.tesseract_cmd = configured_path
#     else:
#         windows_path = r"C:\Program Files\Tesseract-OCR\tesseract.exe"

#         if os.name == "nt" and os.path.exists(windows_path):
#             pytesseract.pytesseract.tesseract_cmd = windows_path

#     return pytesseract

# def _render_page(page, dpi=300):
#     """Render a PDF page for OCR."""
#     scale = dpi / 72

#     pix = page.get_pixmap(
#         matrix=pymupdf.Matrix(scale, scale),
#         alpha=False,
#     )

#     image = Image.open(BytesIO(pix.tobytes("png")))

#     return image, scale

# def _ocr_page(page):
#     """Extract OCR text while retaining visual line and column spacing.

#     This is layout-preserving text reconstruction, not guaranteed table
#     recognition. Original word order and line boundaries are retained.
#     """
#     try:
#         pytesseract = _configure_tesseract()
#         language = os.getenv("TESSERACT_LANG", "eng")
#         dpi = int(os.getenv("OCR_DPI", "300"))
#         image, _ = _render_page(page, dpi)

#         data = pytesseract.image_to_data(
#             image,
#             lang=language,
#             output_type=pytesseract.Output.DICT,
#             config="--psm 3",
#         )

#         lines = {}
#         for index, raw_text in enumerate(data.get("text", [])):
#             word = str(raw_text).strip()
#             if not word:
#                 continue
#             try:
#                 confidence = float(data["conf"][index])
#                 left = int(data["left"][index])
#                 width = int(data["width"][index])
#             except (KeyError, TypeError, ValueError):
#                 continue
#             if confidence < 20:
#                 continue

#             key = (
#                 int(data["block_num"][index]),
#                 int(data["par_num"][index]),
#                 int(data["line_num"][index]),
#             )
#             lines.setdefault(key, []).append((left, width, word))

#         output_lines = []
#         for key in sorted(lines):
#             words = sorted(lines[key], key=lambda item: item[0])
#             if not words:
#                 continue

#             # Approximate visual columns using large horizontal gaps.
#             # Tabs retain column separation for downstream text/chunking.
#             pieces = [words[0][2]]
#             previous_right = words[0][0] + words[0][1]
#             typical_widths = [w for _, w, _ in words if w > 0]
#             average_word_width = (
#                 sum(typical_widths) / len(typical_widths)
#                 if typical_widths else 20
#             )
#             for left, width, word in words[1:]:
#                 gap = max(0, left - previous_right)
#                 if gap >= average_word_width * 0.65:
#                     pieces.append("\t")
#                 else:
#                     pieces.append(" ")
#                 pieces.append(word)
#                 previous_right = left + width
#             output_lines.append("".join(pieces))

#         return "\n".join(output_lines).strip()

#     except ImportError as exc:
#         raise RuntimeError(
#             "OCR dependencies are missing. Install with: "
#             "python -m pip install pytesseract Pillow"
#         ) from exc
#     except Exception as exc:
#         raise RuntimeError(
#             "OCR failed. Check Tesseract installation, language data "
#             f"and TESSERACT_CMD. Original error: {exc}"
#         ) from exc


# def _normalise_token(value):
#     """Normalize a word for matching without changing source text."""
#     return re.sub(r"[^a-z0-9]", "", str(value).lower())

# def _quote_tokens(quote):
#     return [
#         token
#         for token in (_normalise_token(w) for w in (quote or "").split())
#         if token
#     ]

# def _group_word_rectangles(words, page_width, page_height):
#     """
#     Convert matched word boxes into one rectangle per visual text line.

#     Each input item:
#         (x0, y0, x1, y1, text, line_key)
#     """
#     if not words:
#         return []

#     grouped = {}

#     for word in words:
#         x0, y0, x1, y1 = word[:4]
#         line_key = word[5]

#         grouped.setdefault(line_key, []).append(
#             (x0, y0, x1, y1)
#         )

#     results = []

#     for line_words in grouped.values():
#         results.append({
#             "x0": float(min(w[0] for w in line_words)),
#             "y0": float(min(w[1] for w in line_words)),
#             "x1": float(max(w[2] for w in line_words)),
#             "y1": float(max(w[3] for w in line_words)),
#             "page_width": float(page_width),
#             "page_height": float(page_height),
#         })

#     return results

# def _find_word_sequence(word_entries, quote, page):
#     """
#     Match a quotation against extracted word boxes.

#     Exact normalized token matching is preferred.
#     Fuzzy matching is used only when an exact match is unavailable.
#     """
#     quote_words = _quote_tokens(quote)

#     if len(quote_words) < 3:
#         return []

#     source_words = [
#         _normalise_token(entry[4])
#         for entry in word_entries
#     ]

#     if not source_words:
#         return []

#     target_length = len(quote_words)

#     # First: exact contiguous token sequence.
#     for start in range(len(source_words) - target_length + 1):
#         candidate = source_words[start:start + target_length]

#         if candidate == quote_words:
#             matched = word_entries[start:start + target_length]

#             return _group_word_rectangles(
#                 matched,
#                 page.rect.width,
#                 page.rect.height,
#             )

#     # Second: cautious fuzzy matching.
#     # Do not accept short or weak matches as complete quotations.
#     if target_length < 5:
#         return []

#     best_score = 0
#     best_start = None
#     best_length = None

#     minimum_length = max(3, target_length - 2)
#     maximum_length = min(
#         len(source_words),
#         target_length + 2,
#     )

#     for window_length in range(minimum_length, maximum_length + 1):
#         for start in range(
#             len(source_words) - window_length + 1
#         ):
#             candidate = source_words[
#                 start:start + window_length
#             ]

#             score = SequenceMatcher(
#                 None,
#                 quote_words,
#                 candidate,
#                 autojunk=False,
#             ).ratio()

#             if score > best_score:
#                 best_score = score
#                 best_start = start
#                 best_length = window_length

#     # Conservative threshold to avoid highlighting unrelated text.
#     if (
#         best_start is None
#         or best_score < 0.92
#         or best_length is None
#     ):
#         return []

#     matched = word_entries[
#         best_start:best_start + best_length
#     ]

#     logger.info(
#         "Fuzzy PDF quotation match: score=%.3f, "
#         "quote_words=%s, matched_words=%s",
#         best_score,
#         target_length,
#         best_length,
#     )

#     return _group_word_rectangles(
#         matched,
#         page.rect.width,
#         page.rect.height,
#     )

# def _native_word_entries(page):
#     """Read native PDF words with their coordinates."""
#     entries = []

#     for word in page.get_text("words"):
#         # PyMuPDF word tuple:
#         # x0, y0, x1, y1, word, block_no, line_no, word_no
#         x0, y0, x1, y1, text = word[:5]
#         block_no = word[5]
#         line_no = word[6]

#         entries.append((
#             float(x0),
#             float(y0),
#             float(x1),
#             float(y1),
#             str(text),
#             (block_no, line_no),
#         ))

#     return entries

# def _ocr_word_entries(page):
#     """OCR the page and return word-level PDF coordinates."""
#     try:
#         pytesseract = _configure_tesseract()

#         language = os.getenv("TESSERACT_LANG", "eng")
#         dpi = int(os.getenv("OCR_DPI", "300"))

#         image, scale = _render_page(page, dpi)

#         data = pytesseract.image_to_data(
#             image,
#             lang=language,
#             output_type=pytesseract.Output.DICT,
#             config="--psm 3",
#         )

#         entries = []

#         count = len(data["text"])

#         for index in range(count):
#             text = str(data["text"][index]).strip()

#             if not text:
#                 continue

#             try:
#                 confidence = float(data["conf"][index])
#             except (TypeError, ValueError):
#                 continue

#             # Ignore extremely unreliable OCR words.
#             if confidence < 35:
#                 continue

#             left = float(data["left"][index])
#             top = float(data["top"][index])
#             width = float(data["width"][index])
#             height = float(data["height"][index])

#             # Convert rendered image pixels back to PDF coordinates.
#             x0 = page.rect.x0 + left / scale
#             y0 = page.rect.y0 + top / scale
#             x1 = page.rect.x0 + (left + width) / scale
#             y1 = page.rect.y0 + (top + height) / scale

#             line_key = (
#                 data["block_num"][index],
#                 data["par_num"][index],
#                 data["line_num"][index],
#             )

#             entries.append((
#                 x0,
#                 y0,
#                 x1,
#                 y1,
#                 text,
#                 line_key,
#             ))

#         return entries

#     except ImportError as exc:
#         raise RuntimeError(
#             "OCR dependencies are missing. Install pytesseract and Pillow."
#         ) from exc

# def extract_pdf(path: str):
#     """
#     Extract text page by page.

#     Existing return structure is preserved:
#         page_number, text, width, height, ocr_used
#     """
#     pages = []

#     with pymupdf.open(path) as doc:
#         for index, page in enumerate(doc):
#             native_text = (
#                 page.get_text("text") or ""
#             ).strip()

#             text = native_text
#             ocr_used = False

#             if len(native_text) < 40:
#                 try:
#                     ocr_text = _ocr_page(page)

#                     if len(ocr_text) > len(native_text):
#                         text = ocr_text
#                         ocr_used = True

#                 except Exception:
#                     logger.exception(
#                         "OCR extraction failed for page %s of %s",
#                         index + 1,
#                         path,
#                     )

#             pages.append({
#                 "page_number": index + 1,
#                 "text": text,
#                 "width": float(page.rect.width),
#                 "height": float(page.rect.height),
#                 "ocr_used": ocr_used,
#             })

#     return pages

# def split_text(text: str, max_chars: int = 1500, overlap: int = 180):
#     """Split text without flattening meaningful OCR/PDF line boundaries.

#     Prefer paragraph/line boundaries. Long individual lines are still split
#     safely, with the existing character limit and overlap semantics.
#     """
#     clean = (text or "").replace("\r\n", "\n").replace("\r", "\n")
#     clean = re.sub(r"[ \t]+\n", "\n", clean)
#     clean = re.sub(r"\n{3,}", "\n\n", clean).strip()
#     if not clean:
#         return []

#     chunks = []
#     current = ""

#     def emit(value):
#         value = value.strip()
#         if value:
#             chunks.append(value)

#     # Keep each OCR visual line intact whenever it fits in a chunk.
#     units = clean.splitlines()
#     for unit in units:
#         unit = unit.strip()
#         if not unit:
#             if current and not current.endswith("\n"):
#                 current += "\n"
#             continue

#         if len(unit) > max_chars:
#             if current.strip():
#                 emit(current)
#                 current = ""
#             position = 0
#             while position < len(unit):
#                 end = min(position + max_chars, len(unit))
#                 if end < len(unit):
#                     boundary = unit.rfind(" ", position, end)
#                     if boundary > position + max_chars // 2:
#                         end = boundary
#                 emit(unit[position:end])
#                 if end >= len(unit):
#                     break
#                 position = max(end - overlap, position + 1)
#             continue

#         candidate = unit if not current else current.rstrip() + "\n" + unit
#         if len(candidate) <= max_chars:
#             current = candidate
#         else:
#             previous = current.rstrip()
#             emit(previous)
#             carry = previous[-overlap:] if overlap > 0 else ""
#             current = (carry + "\n" if carry else "") + unit

#     emit(current)
#     return chunks

# def find_highlight_rects(
#     pdf_path: str,
#     page_number: int,
#     quote: str,
# ):
#     """
#     Locate quotation coordinates using:

#     1. Native exact PDF search.
#     2. Native normalized word matching.
#     3. OCR word-level coordinate matching.

#     Returns the same coordinate dictionary format expected by
#     the existing analysis.py citation persistence logic.
#     """
#     if not quote or not quote.strip():
#         return []

#     try:
#         with pymupdf.open(pdf_path) as doc:
#             if page_number < 1 or page_number > len(doc):
#                 logger.warning(
#                     "Invalid highlight page: %s in %s",
#                     page_number,
#                     pdf_path,
#                 )
#                 return []

#             page = doc[page_number - 1]

#             # 1. Native exact search.
#             rects = page.search_for(
#                 quote.strip(),
#                 quads=False,
#             )

#             if rects:
#                 logger.info(
#                     "Native exact highlight match: file=%s page=%s "
#                     "rectangles=%s",
#                     os.path.basename(pdf_path),
#                     page_number,
#                     len(rects),
#                 )

#                 return [
#                     {
#                         "x0": float(r.x0),
#                         "y0": float(r.y0),
#                         "x1": float(r.x1),
#                         "y1": float(r.y1),
#                         "page_width": float(page.rect.width),
#                         "page_height": float(page.rect.height),
#                     }
#                     for r in rects
#                 ]

#             # 2. Native word-level normalized matching.
#             native_words = _native_word_entries(page)

#             rects = _find_word_sequence(
#                 native_words,
#                 quote,
#                 page,
#             )

#             if rects:
#                 logger.info(
#                     "Native normalized highlight match: file=%s "
#                     "page=%s rectangles=%s",
#                     os.path.basename(pdf_path),
#                     page_number,
#                     len(rects),
#                 )

#                 return rects

#             # 3. OCR word-level matching.
#             logger.info(
#                 "Native matching failed; trying OCR: file=%s page=%s",
#                 os.path.basename(pdf_path),
#                 page_number,
#             )

#             ocr_words = _ocr_word_entries(page)

#             rects = _find_word_sequence(
#                 ocr_words,
#                 quote,
#                 page,
#             )

#             if rects:
#                 logger.info(
#                     "OCR highlight match: file=%s page=%s "
#                     "rectangles=%s",
#                     os.path.basename(pdf_path),
#                     page_number,
#                     len(rects),
#                 )

#                 return rects

#             logger.warning(
#                 "No highlight match found: file=%s page=%s quote=%r",
#                 os.path.basename(pdf_path),
#                 page_number,
#                 quote[:200],
#             )

#             return []

#     except Exception:
#         logger.exception(
#             "Highlight generation failed: file=%s page=%s",
#             pdf_path,
#             page_number,
#         )

#         return []




# Phase 1



# import os
# import re
# import logging
# import unicodedata
# from io import BytesIO
# from difflib import SequenceMatcher
# import pymupdf
# from PIL import Image

# logger = logging.getLogger(__name__)

# def _configure_tesseract():
#     import pytesseract
#     configured_path = os.getenv("TESSERACT_CMD")

#     if configured_path:
#         pytesseract.pytesseract.tesseract_cmd = configured_path
#     else:
#         windows_path = r"C:\Program Files\Tesseract-OCR\tesseract.exe"

#         if os.name == "nt" and os.path.exists(windows_path):
#             pytesseract.pytesseract.tesseract_cmd = windows_path

#     return pytesseract

# def _render_page(page, dpi=300):
#     """Render a PDF page for OCR."""
#     scale = dpi / 72

#     pix = page.get_pixmap(
#         matrix=pymupdf.Matrix(scale, scale),
#         alpha=False,
#     )

#     image = Image.open(BytesIO(pix.tobytes("png")))

#     return image, scale

# def _ocr_page(page):
#     """Extract OCR text while retaining visual line and column spacing.

#     This is layout-preserving text reconstruction, not guaranteed table
#     recognition. Original word order and line boundaries are retained.
#     """
#     try:
#         pytesseract = _configure_tesseract()
#         language = os.getenv("TESSERACT_LANG", "eng+hin")
#         dpi = int(os.getenv("OCR_DPI", "300"))
#         image, _ = _render_page(page, dpi)

#         data = pytesseract.image_to_data(
#             image,
#             lang=language,
#             output_type=pytesseract.Output.DICT,
#             config="--psm 3",
#         )

#         lines = {}
#         for index, raw_text in enumerate(data.get("text", [])):
#             word = str(raw_text).strip()
#             if not word:
#                 continue
#             try:
#                 confidence = float(data["conf"][index])
#                 left = int(data["left"][index])
#                 width = int(data["width"][index])
#             except (KeyError, TypeError, ValueError):
#                 continue
#             if confidence < 20:
#                 continue

#             key = (
#                 int(data["block_num"][index]),
#                 int(data["par_num"][index]),
#                 int(data["line_num"][index]),
#             )
#             lines.setdefault(key, []).append((left, width, word))

#         output_lines = []
#         for key in sorted(lines):
#             words = sorted(lines[key], key=lambda item: item[0])
#             if not words:
#                 continue

#             # Approximate visual columns using large horizontal gaps.
#             # Tabs retain column separation for downstream text/chunking.
#             pieces = [words[0][2]]
#             previous_right = words[0][0] + words[0][1]
#             typical_widths = [w for _, w, _ in words if w > 0]
#             average_word_width = (
#                 sum(typical_widths) / len(typical_widths)
#                 if typical_widths else 20
#             )
#             for left, width, word in words[1:]:
#                 gap = max(0, left - previous_right)
#                 if gap >= average_word_width * 0.65:
#                     pieces.append("\t")
#                 else:
#                     pieces.append(" ")
#                 pieces.append(word)
#                 previous_right = left + width
#             output_lines.append("".join(pieces))

#         return "\n".join(output_lines).strip()

#     except ImportError as exc:
#         raise RuntimeError(
#             "OCR dependencies are missing. Install with: "
#             "python -m pip install pytesseract Pillow"
#         ) from exc
#     except Exception as exc:
#         raise RuntimeError(
#             "OCR failed. Check Tesseract installation, language data "
#             f"and TESSERACT_CMD. Original error: {exc}"
#         ) from exc


# def _normalise_token(value):
#     """Normalize a word for matching while preserving Unicode scripts.

#     Devanagari vowel signs and other combining marks are not ASCII letters.
#     Retain Unicode letters, numbers, and combining marks so Hindi quotations
#     can be matched for source highlighting just like English quotations.
#     The original extracted/source text is never modified.
#     """
#     normalized = str(value).casefold()
#     return "".join(
#         char
#         for char in normalized
#         if char.isalnum() or unicodedata.category(char).startswith("M")
#     )

# def _quote_tokens(quote):
#     return [
#         token
#         for token in (_normalise_token(w) for w in (quote or "").split())
#         if token
#     ]

# def _group_word_rectangles(words, page_width, page_height):
#     """
#     Convert matched word boxes into one rectangle per visual text line.

#     Each input item:
#         (x0, y0, x1, y1, text, line_key)
#     """
#     if not words:
#         return []

#     grouped = {}

#     for word in words:
#         x0, y0, x1, y1 = word[:4]
#         line_key = word[5]

#         grouped.setdefault(line_key, []).append(
#             (x0, y0, x1, y1)
#         )

#     results = []

#     for line_words in grouped.values():
#         results.append({
#             "x0": float(min(w[0] for w in line_words)),
#             "y0": float(min(w[1] for w in line_words)),
#             "x1": float(max(w[2] for w in line_words)),
#             "y1": float(max(w[3] for w in line_words)),
#             "page_width": float(page_width),
#             "page_height": float(page_height),
#         })

#     return results

# def _find_word_sequence(word_entries, quote, page):
#     """
#     Match a quotation against extracted word boxes.

#     Exact normalized token matching is preferred.
#     Fuzzy matching is used only when an exact match is unavailable.
#     """
#     quote_words = _quote_tokens(quote)

#     if len(quote_words) < 3:
#         return []

#     source_words = [
#         _normalise_token(entry[4])
#         for entry in word_entries
#     ]

#     if not source_words:
#         return []

#     target_length = len(quote_words)

#     # First: exact contiguous token sequence.
#     for start in range(len(source_words) - target_length + 1):
#         candidate = source_words[start:start + target_length]

#         if candidate == quote_words:
#             matched = word_entries[start:start + target_length]

#             return _group_word_rectangles(
#                 matched,
#                 page.rect.width,
#                 page.rect.height,
#             )

#     # Second: cautious fuzzy matching.
#     # Do not accept short or weak matches as complete quotations.
#     if target_length < 5:
#         return []

#     best_score = 0
#     best_start = None
#     best_length = None

#     minimum_length = max(3, target_length - 2)
#     maximum_length = min(
#         len(source_words),
#         target_length + 2,
#     )

#     for window_length in range(minimum_length, maximum_length + 1):
#         for start in range(
#             len(source_words) - window_length + 1
#         ):
#             candidate = source_words[
#                 start:start + window_length
#             ]

#             score = SequenceMatcher(
#                 None,
#                 quote_words,
#                 candidate,
#                 autojunk=False,
#             ).ratio()

#             if score > best_score:
#                 best_score = score
#                 best_start = start
#                 best_length = window_length

#     # Conservative threshold to avoid highlighting unrelated text.
#     if (
#         best_start is None
#         or best_score < 0.92
#         or best_length is None
#     ):
#         return []

#     matched = word_entries[
#         best_start:best_start + best_length
#     ]

#     logger.info(
#         "Fuzzy PDF quotation match: score=%.3f, "
#         "quote_words=%s, matched_words=%s",
#         best_score,
#         target_length,
#         best_length,
#     )

#     return _group_word_rectangles(
#         matched,
#         page.rect.width,
#         page.rect.height,
#     )

# def _native_word_entries(page):
#     """Read native PDF words with their coordinates."""
#     entries = []

#     for word in page.get_text("words"):
#         # PyMuPDF word tuple:
#         # x0, y0, x1, y1, word, block_no, line_no, word_no
#         x0, y0, x1, y1, text = word[:5]
#         block_no = word[5]
#         line_no = word[6]

#         entries.append((
#             float(x0),
#             float(y0),
#             float(x1),
#             float(y1),
#             str(text),
#             (block_no, line_no),
#         ))

#     return entries

# def _ocr_word_entries(page):
#     """OCR the page and return word-level PDF coordinates."""
#     try:
#         pytesseract = _configure_tesseract()

#         language = os.getenv("TESSERACT_LANG", "eng+hin")
#         dpi = int(os.getenv("OCR_DPI", "300"))

#         image, scale = _render_page(page, dpi)

#         data = pytesseract.image_to_data(
#             image,
#             lang=language,
#             output_type=pytesseract.Output.DICT,
#             config="--psm 3",
#         )

#         entries = []

#         count = len(data["text"])

#         for index in range(count):
#             text = str(data["text"][index]).strip()

#             if not text:
#                 continue

#             try:
#                 confidence = float(data["conf"][index])
#             except (TypeError, ValueError):
#                 continue

#             # Ignore extremely unreliable OCR words.
#             if confidence < 35:
#                 continue

#             left = float(data["left"][index])
#             top = float(data["top"][index])
#             width = float(data["width"][index])
#             height = float(data["height"][index])

#             # Convert rendered image pixels back to PDF coordinates.
#             x0 = page.rect.x0 + left / scale
#             y0 = page.rect.y0 + top / scale
#             x1 = page.rect.x0 + (left + width) / scale
#             y1 = page.rect.y0 + (top + height) / scale

#             line_key = (
#                 data["block_num"][index],
#                 data["par_num"][index],
#                 data["line_num"][index],
#             )

#             entries.append((
#                 x0,
#                 y0,
#                 x1,
#                 y1,
#                 text,
#                 line_key,
#             ))

#         return entries

#     except ImportError as exc:
#         raise RuntimeError(
#             "OCR dependencies are missing. Install pytesseract and Pillow."
#         ) from exc

# def extract_pdf(path: str):
#     """
#     Extract text page by page.

#     Existing return structure is preserved:
#         page_number, text, width, height, ocr_used
#     """
#     pages = []

#     with pymupdf.open(path) as doc:
#         for index, page in enumerate(doc):
#             native_text = (
#                 page.get_text("text") or ""
#             ).strip()

#             text = native_text
#             ocr_used = False

#             if len(native_text) < 40:
#                 try:
#                     ocr_text = _ocr_page(page)

#                     if len(ocr_text) > len(native_text):
#                         text = ocr_text
#                         ocr_used = True

#                 except Exception:
#                     logger.exception(
#                         "OCR extraction failed for page %s of %s",
#                         index + 1,
#                         path,
#                     )

#             pages.append({
#                 "page_number": index + 1,
#                 "text": text,
#                 "width": float(page.rect.width),
#                 "height": float(page.rect.height),
#                 "ocr_used": ocr_used,
#             })

#     return pages

# def split_text(text: str, max_chars: int = 1500, overlap: int = 180):
#     """Split text without flattening meaningful OCR/PDF line boundaries.

#     Prefer paragraph/line boundaries. Long individual lines are still split
#     safely, with the existing character limit and overlap semantics.
#     """
#     clean = (text or "").replace("\r\n", "\n").replace("\r", "\n")
#     clean = re.sub(r"[ \t]+\n", "\n", clean)
#     clean = re.sub(r"\n{3,}", "\n\n", clean).strip()
#     if not clean:
#         return []

#     chunks = []
#     current = ""

#     def emit(value):
#         value = value.strip()
#         if value:
#             chunks.append(value)

#     # Keep each OCR visual line intact whenever it fits in a chunk.
#     units = clean.splitlines()
#     for unit in units:
#         unit = unit.strip()
#         if not unit:
#             if current and not current.endswith("\n"):
#                 current += "\n"
#             continue

#         if len(unit) > max_chars:
#             if current.strip():
#                 emit(current)
#                 current = ""
#             position = 0
#             while position < len(unit):
#                 end = min(position + max_chars, len(unit))
#                 if end < len(unit):
#                     boundary = unit.rfind(" ", position, end)
#                     if boundary > position + max_chars // 2:
#                         end = boundary
#                 emit(unit[position:end])
#                 if end >= len(unit):
#                     break
#                 position = max(end - overlap, position + 1)
#             continue

#         candidate = unit if not current else current.rstrip() + "\n" + unit
#         if len(candidate) <= max_chars:
#             current = candidate
#         else:
#             previous = current.rstrip()
#             emit(previous)
#             carry = previous[-overlap:] if overlap > 0 else ""
#             current = (carry + "\n" if carry else "") + unit

#     emit(current)
#     return chunks

# def find_highlight_rects(
#     pdf_path: str,
#     page_number: int,
#     quote: str,
# ):
#     """
#     Locate quotation coordinates using:

#     1. Native exact PDF search.
#     2. Native normalized word matching.
#     3. OCR word-level coordinate matching.

#     Returns the same coordinate dictionary format expected by
#     the existing analysis.py citation persistence logic.
#     """
#     if not quote or not quote.strip():
#         return []

#     try:
#         with pymupdf.open(pdf_path) as doc:
#             if page_number < 1 or page_number > len(doc):
#                 logger.warning(
#                     "Invalid highlight page: %s in %s",
#                     page_number,
#                     pdf_path,
#                 )
#                 return []

#             page = doc[page_number - 1]

#             # 1. Native exact search.
#             rects = page.search_for(
#                 quote.strip(),
#                 quads=False,
#             )

#             if rects:
#                 logger.info(
#                     "Native exact highlight match: file=%s page=%s "
#                     "rectangles=%s",
#                     os.path.basename(pdf_path),
#                     page_number,
#                     len(rects),
#                 )

#                 return [
#                     {
#                         "x0": float(r.x0),
#                         "y0": float(r.y0),
#                         "x1": float(r.x1),
#                         "y1": float(r.y1),
#                         "page_width": float(page.rect.width),
#                         "page_height": float(page.rect.height),
#                     }
#                     for r in rects
#                 ]

#             # 2. Native word-level normalized matching.
#             native_words = _native_word_entries(page)

#             rects = _find_word_sequence(
#                 native_words,
#                 quote,
#                 page,
#             )

#             if rects:
#                 logger.info(
#                     "Native normalized highlight match: file=%s "
#                     "page=%s rectangles=%s",
#                     os.path.basename(pdf_path),
#                     page_number,
#                     len(rects),
#                 )

#                 return rects

#             # 3. OCR word-level matching.
#             logger.info(
#                 "Native matching failed; trying OCR: file=%s page=%s",
#                 os.path.basename(pdf_path),
#                 page_number,
#             )

#             ocr_words = _ocr_word_entries(page)

#             rects = _find_word_sequence(
#                 ocr_words,
#                 quote,
#                 page,
#             )

#             if rects:
#                 logger.info(
#                     "OCR highlight match: file=%s page=%s "
#                     "rectangles=%s",
#                     os.path.basename(pdf_path),
#                     page_number,
#                     len(rects),
#                 )

#                 return rects

#             logger.warning(
#                 "No highlight match found: file=%s page=%s quote=%r",
#                 os.path.basename(pdf_path),
#                 page_number,
#                 quote[:200],
#             )

#             return []

#     except Exception:
#         logger.exception(
#             "Highlight generation failed: file=%s page=%s",
#             pdf_path,
#             page_number,
#         )

#         return []


# Phase 2


import os
import re
import logging
import unicodedata
from io import BytesIO
from difflib import SequenceMatcher
import pymupdf
from PIL import Image

logger = logging.getLogger(__name__)

def _page_contains_devanagari(page):
    text = page.get_text("text") or ""
    return bool(re.search(r"[\u0900-\u097F]", text))

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
    """Extract OCR text while retaining visual line and column spacing.

    This is layout-preserving text reconstruction, not guaranteed table
    recognition. Original word order and line boundaries are retained.
    """
    try:
        pytesseract = _configure_tesseract()
        language = os.getenv("TESSERACT_LANG", "eng+hin")
        dpi = int(os.getenv("OCR_DPI", "300"))
        image, _ = _render_page(page, dpi)

        data = pytesseract.image_to_data(
            image,
            lang=language,
            output_type=pytesseract.Output.DICT,
            config="--psm 3",
        )

        lines = {}
        for index, raw_text in enumerate(data.get("text", [])):
            word = str(raw_text).strip()
            if not word:
                continue
            try:
                confidence = float(data["conf"][index])
                left = int(data["left"][index])
                width = int(data["width"][index])
            except (KeyError, TypeError, ValueError):
                continue
            if confidence < 20:
                continue

            key = (
                int(data["block_num"][index]),
                int(data["par_num"][index]),
                int(data["line_num"][index]),
            )
            lines.setdefault(key, []).append((left, width, word))

        output_lines = []
        for key in sorted(lines):
            words = sorted(lines[key], key=lambda item: item[0])
            if not words:
                continue

            # Approximate visual columns using large horizontal gaps.
            # Tabs retain column separation for downstream text/chunking.
            pieces = [words[0][2]]
            previous_right = words[0][0] + words[0][1]
            typical_widths = [w for _, w, _ in words if w > 0]
            average_word_width = (
                sum(typical_widths) / len(typical_widths)
                if typical_widths else 20
            )
            for left, width, word in words[1:]:
                gap = max(0, left - previous_right)
                if gap >= average_word_width * 0.65:
                    pieces.append("\t")
                else:
                    pieces.append(" ")
                pieces.append(word)
                previous_right = left + width
            output_lines.append("".join(pieces))

        return "\n".join(output_lines).strip()

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
    """Normalize a word for matching while preserving Unicode scripts.

    Devanagari vowel signs and other combining marks are not ASCII letters.
    Retain Unicode letters, numbers, and combining marks so Hindi quotations
    can be matched for source highlighting just like English quotations.
    The original extracted/source text is never modified.
    """
    # normalized = str(value).casefold()
    normalized = unicodedata.normalize(
        "NFC", str(value)
    ).casefold()
    return "".join(
        char
        for char in normalized
        if char.isalnum() or unicodedata.category(char).startswith("M")
    )

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

        language = os.getenv("TESSERACT_LANG", "eng+hin")
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

def _safe_grapheme_boundary(text: str, position: int) -> int:
    """Move a split position left so it does not detach a Unicode character sequence.

    This lightweight standard-library guard handles combining marks,
    Devanagari virama sequences, and zero-width joiners/non-joiners.
    """
    position = max(0, min(position, len(text)))
    while 0 < position < len(text):
        current = text[position]
        previous = text[position - 1]

        current_category = unicodedata.category(current)
        previous_name = unicodedata.name(previous, "")

        if (
            current_category.startswith("M")
            or current in ("\u200c", "\u200d")
            or previous in ("\u200c", "\u200d")
            or "VIRAMA" in previous_name
            or "HALANT" in previous_name
        ):
            position -= 1
            continue
        break
    return position


def split_text(text: str, max_chars: int = 1500, overlap: int = 180):
    """Split page text into Unicode-safe chunks while preserving line boundaries.

    NFC normalization is applied only to this chunking copy. The caller's
    original extracted page text remains unchanged in DocumentPage.text.
    """
    if not text:
        return []
    if max_chars <= 0:
        raise ValueError("max_chars must be greater than zero")
    if overlap < 0:
        raise ValueError("overlap cannot be negative")

    clean = unicodedata.normalize("NFC", str(text))
    clean = clean.replace("\r\n", "\n").replace("\r", "\n")
    clean = re.sub(r"[ \t]+\n", "\n", clean)
    clean = re.sub(r"\n{3,}", "\n\n", clean).strip()
    if not clean:
        return []

    chunks = []
    current = ""

    def emit(value):
        value = value.strip()
        if value:
            chunks.append(value)

    def split_long_unit(unit):
        position = 0
        while position < len(unit):
            end = min(position + max_chars, len(unit))
            if end < len(unit):
                boundary = unit.rfind(" ", position, end)
                if boundary > position + max_chars // 2:
                    end = boundary
            end = _safe_grapheme_boundary(unit, end)
            if end <= position:
                # A single grapheme may exceed a deliberately tiny limit.
                end = min(position + max_chars, len(unit))
                while end < len(unit) and (
                    unicodedata.category(unit[end]).startswith("M")
                    or unit[end] in ("\u200c", "\u200d")
                    or "VIRAMA" in unicodedata.name(unit[end - 1], "")
                ):
                    end += 1
            emit(unit[position:end])
            if end >= len(unit):
                break
            next_position = max(end - overlap, position + 1)
            position = _safe_grapheme_boundary(unit, next_position)
            if position <= 0:
                position = end

    # Keep OCR visual lines intact whenever they fit in a chunk.
    for unit in clean.splitlines():
        unit = unit.strip()
        if not unit:
            if current and not current.endswith("\n"):
                current += "\n"
            continue

        if len(unit) > max_chars:
            if current.strip():
                emit(current)
                current = ""
            split_long_unit(unit)
            continue

        candidate = unit if not current else current.rstrip() + "\n" + unit
        if len(candidate) <= max_chars:
            current = candidate
        else:
            previous = current.rstrip()
            emit(previous)
            carry_start = _safe_grapheme_boundary(
                previous, max(0, len(previous) - overlap)
            ) if overlap > 0 else len(previous)
            carry = previous[carry_start:] if overlap > 0 else ""
            current = (carry + "\n" if carry else "") + unit

    emit(current)
    return chunks


def find_highlight_rects(pdf_path, page_number, quote):
    if not quote or not quote.strip():
        return []

    try:
        with pymupdf.open(pdf_path) as doc:
            if page_number < 1 or page_number > len(doc):
                return []

            page = doc[page_number - 1]

            is_hindi_page = _page_contains_devanagari(page)

            # Hindi and mixed-script pages: visible-page OCR first.
            if is_hindi_page:
                try:
                    ocr_words = _ocr_word_entries(page)
                    rects = _find_word_sequence(
                        ocr_words, quote, page
                    )

                    if rects:
                        logger.info(
                            "Hindi OCR highlight matched | file=%s | page=%s",
                            os.path.basename(pdf_path),
                            page_number,
                        )
                        return rects

                except Exception:
                    logger.exception(
                        "Hindi OCR highlight failed | file=%s | page=%s",
                        pdf_path,
                        page_number,
                    )

                # Do not fall back to potentially misplaced hidden text-layer
                # coordinates on Hindi pages.
                return []

            # English pages: preserve existing native PDF behaviour.
            rects = page.search_for(quote.strip())

            if rects:
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

            native_words = _native_word_entries(page)
            rects = _find_word_sequence(
                native_words, quote, page
            )

            if rects:
                return rects

            ocr_words = _ocr_word_entries(page)
            return _find_word_sequence(
                ocr_words, quote, page
            )

    except Exception:
        logger.exception(
            "Highlight generation failed | file=%s | page=%s",
            pdf_path,
            page_number,
        )
        return []