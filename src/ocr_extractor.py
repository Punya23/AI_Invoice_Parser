"""
ocr_extractor.py — OCR Extraction for Scanned PDFs using Tesseract

Lightweight OCR pipeline:
1. PyMuPDF (fitz) renders PDF pages to images (no poppler needed)
2. Tesseract OCR extracts text (industry standard, fast, lightweight)
3. Text is reconstructed in reading order

Why Tesseract over PaddleOCR:
- 30MB vs 1.5GB install
- 0.5-1s per page vs 3-5s
- Industry standard (maintained by Google)
- Production-friendly: modular — swap for PaddleOCR/Document AI later

System requirement: brew install tesseract (Mac) / apt install tesseract-ocr (Linux)
"""

import logging
import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class OCRPageResult:
    page_number: int
    text: str = ""
    confidence: float = 0.0


@dataclass
class OCRDocumentResult:
    pdf_path: str
    pages: list[OCRPageResult] = field(default_factory=list)

    @property
    def full_text(self) -> str:
        return "\n\n".join(p.text for p in self.pages if p.text)

    @property
    def overall_confidence(self) -> float:
        confs = [p.confidence for p in self.pages if p.confidence > 0]
        return sum(confs) / len(confs) if confs else 0.0


def _check_tesseract() -> bool:
    """Check if Tesseract is installed."""
    try:
        import pytesseract
        pytesseract.get_tesseract_version()
        return True
    except Exception:
        return False


def _render_page_to_image(pdf_path: str, page_number: int, dpi: int = 300):
    """Render a PDF page to PIL Image using PyMuPDF (no poppler needed)."""
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
    """
    Extract text from scanned PDF using Tesseract OCR.
    
    Pipeline:
    1. Render each page to image via PyMuPDF (pure Python, no poppler)
    2. Run Tesseract OCR on each image
    3. Optionally get per-word confidence via tsv output
    """
    if not _check_tesseract():
        raise ImportError(
            "Tesseract OCR not installed.\n"
            "  Mac:   brew install tesseract\n"
            "  Linux: sudo apt install tesseract-ocr\n"
            "  Then:  pip install pytesseract"
        )

    import pytesseract
    import fitz
    from PIL import Image, ImageFilter

    doc_result = OCRDocumentResult(pdf_path=pdf_path)

    pdf_doc = fitz.open(pdf_path)
    page_count = len(pdf_doc)
    pdf_doc.close()

    for page_num in range(page_count):
        logger.info(f"  OCR page {page_num + 1}/{page_count} of {Path(pdf_path).name}")

        try:
            # Render page to image
            img = _render_page_to_image(pdf_path, page_num, dpi=dpi)

            # Preprocess: convert to grayscale and sharpen for better OCR
            img_gray = img.convert('L')
            img_sharp = img_gray.filter(ImageFilter.SHARPEN)

            # Run Tesseract — get text
            text = pytesseract.image_to_string(img_sharp, lang='eng')

            # Get confidence from TSV output
            try:
                tsv_data = pytesseract.image_to_data(img_sharp, lang='eng', output_type=pytesseract.Output.DICT)
                confidences = [int(c) for c in tsv_data['conf'] if int(c) > 0]
                avg_conf = sum(confidences) / len(confidences) / 100.0 if confidences else 0.0
            except Exception:
                avg_conf = 0.5  # Default if TSV fails

            page_result = OCRPageResult(
                page_number=page_num + 1,
                text=text.strip(),
                confidence=round(avg_conf, 3),
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
