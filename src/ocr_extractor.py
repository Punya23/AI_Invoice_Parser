"""
ocr_extractor.py — OCR Extraction for Scanned PDFs using Tesseract

Advanced OCR pipeline:
1. PyMuPDF (fitz) renders PDF pages to images.
2. Advanced preprocessing (deskew, denoise, adaptive thresholding).
3. Tesseract OCR extracts text and per-word bounding boxes.
4. Positional clustering builds tables from borderless OCR output.
5. CV table extractor handles explicitly bordered tables.
"""

import logging
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

@dataclass
class OCRPageResult:
    page_number: int
    text: str = ""
    confidence: float = 0.0
    tables: list = field(default_factory=list)

@dataclass
class OCRDocumentResult:
    pdf_path: str
    pages: list[OCRPageResult] = field(default_factory=list)

    @property
    def full_text(self) -> str:
        return "\n\n".join(p.text for p in self.pages if p.text)

    @property
    def all_tables(self) -> list:
        tables = []
        for page in self.pages:
            tables.extend(page.tables)
        return tables

    @property
    def overall_confidence(self) -> float:
        confs = [p.confidence for p in self.pages if p.confidence > 0]
        return sum(confs) / len(confs) if confs else 0.0


def _check_tesseract() -> bool:
    try:
        import pytesseract
        pytesseract.get_tesseract_version()
        return True
    except Exception:
        return False


def _render_page_to_image(pdf_path: str, page_number: int, dpi: int = 300):
    import fitz
    from PIL import Image

    doc = fitz.open(pdf_path)
    page = doc[page_number]
    zoom = dpi / 72
    mat = fitz.Matrix(zoom, zoom)
    pix = page.get_pixmap(matrix=mat)
    img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
    doc.close()
    return img


def extract_with_ocr(pdf_path: str, dpi: int = 300) -> OCRDocumentResult:
    if not _check_tesseract():
        raise ImportError("Tesseract OCR not installed.")

    import pytesseract
    import fitz
    from PIL import Image
    from src.cv_table_extractor import extract_tables_from_image
    from src.image_preprocessing import preprocess_image_for_ocr
    from src.text_extractor import _cluster_rows, _find_columns, _build_grid, Word

    doc_result = OCRDocumentResult(pdf_path=pdf_path)

    pdf_doc = fitz.open(pdf_path)
    page_count = len(pdf_doc)
    pdf_doc.close()

    for page_num in range(page_count):
        logger.info(f"  OCR page {page_num + 1}/{page_count} of {Path(pdf_path).name}")

        try:
            # 1. Render and Preprocess
            img = _render_page_to_image(pdf_path, page_num, dpi=dpi)
            
            # Use our advanced preprocessing!
            # preprocess_image_for_ocr returns a numpy array (thresh), we need to convert back to PIL for Tesseract
            preprocessed_np = preprocess_image_for_ocr(img)
            preprocessed_img = Image.fromarray(preprocessed_np)

            # 2. Extract with Tesseract TSV data for word bounding boxes
            words = []
            avg_conf = 0.5
            
            try:
                tsv_data = pytesseract.image_to_data(preprocessed_img, lang='eng', output_type=pytesseract.Output.DICT)
                confidences = []
                
                n_boxes = len(tsv_data['text'])
                for i in range(n_boxes):
                    w_text = tsv_data['text'][i].strip()
                    conf = int(tsv_data['conf'][i])
                    if conf >= 0:
                        confidences.append(conf)
                        if w_text:
                            # tsv gives left, top, width, height
                            x0 = float(tsv_data['left'][i])
                            y0 = float(tsv_data['top'][i])
                            w = float(tsv_data['width'][i])
                            h = float(tsv_data['height'][i])
                            words.append(Word(text=w_text, x0=x0, y0=y0, x1=x0+w, y1=y0+h))
                            
                avg_conf = sum(confidences) / len(confidences) / 100.0 if confidences else 0.0
            except Exception as e:
                logger.warning(f"  Tesseract TSV extraction failed: {e}")

            # Reconstruct page text horizontally using Y-coordinate clustering
            if words:
                # DPI-scaled Y threshold
                y_threshold = 35.0 * (dpi / 300.0)
                sorted_words = sorted(words, key=lambda w: (w.y0 + w.y1) / 2)
                rows = []
                
                for word in sorted_words:
                    w_cy = (word.y0 + word.y1) / 2
                    matched_row_idx = -1
                    min_dist = float('inf')
                    for idx, row in enumerate(rows):
                        row_cy = sum((wd.y0 + wd.y1)/2 for wd in row) / len(row)
                        dist = abs(w_cy - row_cy)
                        if dist <= y_threshold and dist < min_dist:
                            min_dist = dist
                            matched_row_idx = idx
                    if matched_row_idx >= 0:
                        rows[matched_row_idx].append(word)
                    else:
                        rows.append([word])
                        
                for row in rows:
                    row.sort(key=lambda w: w.x0)
                    
                rows.sort(key=lambda r: sum((wd.y0 + wd.y1)/2 for wd in r) / len(r))
                
                lines = []
                for row in rows:
                    line_text = " ".join(w.text for w in row).strip()
                    if line_text in ("|", "", ".", "||"):
                        continue
                    lines.append(line_text)
                text = "\n".join(lines)
            else:
                try:
                    text = pytesseract.image_to_string(preprocessed_img, lang='eng')
                except Exception as e:
                    logger.warning(f"  Tesseract image_to_string fallback failed: {e}")
                    text = ""

            # 3. Extract tables
            tables = []
            
            # Tier 1: OpenCV explicitly bordered tables
            try:
                tables = extract_tables_from_image(img) # We use original image for CV because borders might be thresholded out
            except Exception as e:
                logger.warning(f"  CV Table extraction failed on page {page_num + 1}: {e}")
                
            # Tier 2: Positional clustering on OCR words for borderless tables
            if not tables and len(words) > 10:
                try:
                    rows = _cluster_rows(words)
                    if len(rows) >= 3:
                        boundaries = _find_columns(rows)
                        if len(boundaries) >= 3:
                            grid = _build_grid(rows, boundaries)
                            # Quality gate: reject if most cells are empty
                            total_cells = sum(len(r) for r in grid)
                            non_empty = sum(1 for r in grid for c in r if c.strip())
                            fill_ratio = non_empty / total_cells if total_cells > 0 else 0
                            # Word clustering often creates many empty padding cells, so use a very low threshold (2%)
                            if fill_ratio >= 0.02:
                                tables = [grid]
                                logger.debug(f"  Clustered OCR table: {len(grid)} rows × {len(boundaries)} cols, fill={fill_ratio:.0%}")
                            else:
                                logger.debug(f"  Clustered table rejected: {fill_ratio:.0%} fill rate")
                except Exception as e:
                    logger.warning(f"  OCR table clustering failed on page {page_num + 1}: {e}")

            page_result = OCRPageResult(
                page_number=page_num + 1,
                text=text.strip(),
                confidence=round(avg_conf, 3),
                tables=tables
            )

        except Exception as e:
            logger.error(f"  OCR failed on page {page_num + 1}: {e}")
            page_result = OCRPageResult(
                page_number=page_num + 1,
                text="",
                confidence=0.0,
            )

        doc_result.pages.append(page_result)

    logger.info(
        f"  OCR complete: {page_count} pages | "
        f"avg confidence: {doc_result.overall_confidence:.0%}"
    )
    return doc_result
