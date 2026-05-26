"""
pdf_detector.py — PDF Type Classification

Classifies each PDF as digital or scanned before choosing extraction path.
Digital PDFs → pdfplumber (fast, accurate)
Scanned PDFs → OCR (PaddleOCR fallback)

This is the gating decision that avoids unnecessary OCR on digital PDFs.
"""

import logging
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

TEXT_THRESHOLD = 50  # min chars per page to be considered "has text"


@dataclass
class PageProfile:
    page_number: int
    char_count: int
    has_text: bool
    has_images: bool


@dataclass
class PDFProfile:
    path: str
    filename: str
    page_count: int
    pdf_type: str           # "digital" | "scanned" | "hybrid" | "corrupt"
    pages: list[PageProfile]
    digital_pages: int
    scanned_pages: int
    recommended_extractor: str  # "pdfplumber" | "ocr" | "mixed"
    reason: str


def classify_pdf(pdf_path: str) -> PDFProfile:
    """
    Analyze every page and classify the PDF.
    
    Strategy:
    - Open with pdfplumber, extract text per page
    - If chars > threshold → digital page
    - Aggregate to classify the full document
    """
    import pdfplumber

    path = Path(pdf_path)
    if not path.exists():
        raise FileNotFoundError(f"PDF not found: {pdf_path}")

    pages: list[PageProfile] = []

    try:
        with pdfplumber.open(pdf_path) as pdf:
            page_count = len(pdf.pages)

            for i, page in enumerate(pdf.pages, start=1):
                try:
                    text = page.extract_text() or ""
                    char_count = len(text.strip())
                    has_images = len(page.images) > 0

                    pages.append(PageProfile(
                        page_number=i,
                        char_count=char_count,
                        has_text=char_count >= TEXT_THRESHOLD,
                        has_images=has_images,
                    ))
                except Exception as e:
                    logger.warning(f"Page {i} analysis failed: {e}")
                    pages.append(PageProfile(
                        page_number=i, char_count=0,
                        has_text=False, has_images=True,
                    ))

    except Exception as e:
        logger.error(f"Cannot open PDF {pdf_path}: {e}")
        return PDFProfile(
            path=str(pdf_path), filename=path.name,
            page_count=0, pdf_type="corrupt",
            pages=[], digital_pages=0, scanned_pages=0,
            recommended_extractor="none",
            reason=f"Cannot open PDF: {e}",
        )

    digital_count = sum(1 for p in pages if p.has_text)
    scanned_count = page_count - digital_count

    if digital_count == page_count:
        pdf_type = "digital"
        extractor = "pdfplumber"
        reason = f"All {page_count} pages have embedded text layer"
    elif scanned_count == page_count:
        pdf_type = "scanned"
        extractor = "ocr"
        reason = f"All {page_count} pages are image-only (no text layer)"
    else:
        pdf_type = "hybrid"
        extractor = "mixed"
        reason = f"{digital_count} digital + {scanned_count} scanned pages"

    profile = PDFProfile(
        path=str(pdf_path),
        filename=path.name,
        page_count=page_count,
        pdf_type=pdf_type,
        pages=pages,
        digital_pages=digital_count,
        scanned_pages=scanned_count,
        recommended_extractor=extractor,
        reason=reason,
    )

    logger.info(f"[CLASSIFY] {path.name} → {pdf_type.upper()} ({extractor}) | {reason}")
    return profile
