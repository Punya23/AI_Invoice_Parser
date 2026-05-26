"""
main.py — Accuron AI Invoice Parser CLI

Entry point that orchestrates the full pipeline:
PDF → Classify → Extract → Parse → Validate → Excel

Usage:
    python main.py --input Document/ --output output/result.xlsx
    python main.py --input Document/invoice.pdf --output output/result.xlsx
"""

import argparse
import logging
import sys
import time
from pathlib import Path

from src.pdf_detector import classify_pdf
from src.text_extractor import extract_document
from src.invoice_parser import parse_invoice
from src.validator import InvoiceValidator
from src.excel_generator import generate_workbook
from src.models import Invoice, PDFType, ExtractionSource

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)-7s | %(message)s',
    datefmt='%H:%M:%S',
)
logger = logging.getLogger(__name__)


def process_single_invoice(pdf_path: str) -> Invoice:
    """
    Process a single PDF through the full pipeline.
    
    Pipeline:
    1. Classify (digital vs scanned)
    2. Extract (pdfplumber or OCR)
    3. Parse (regex + tables)
    4. Validate (GSTIN, math, completeness)
    """
    filename = Path(pdf_path).name
    logger.info(f"{'─' * 60}")
    logger.info(f"Processing: {filename}")

    # Step 1: Classify PDF type
    profile = classify_pdf(pdf_path)
    logger.info(f"  Type: {profile.pdf_type.upper()} | Pages: {profile.page_count} | {profile.reason}")

    if profile.pdf_type == "corrupt":
        inv = Invoice(source_file=filename, pdf_type=PDFType.CORRUPT)
        inv.processing_errors.append(f"Corrupt PDF: {profile.reason}")
        return inv

    # Step 2: Extract text and tables
    text = ""
    tables = []
    extraction_source = ExtractionSource.PDFPLUMBER_TEXT

    if profile.pdf_type in ("digital", "hybrid"):
        doc = extract_document(pdf_path)
        text = doc.full_text
        tables = doc.all_tables
        extraction_source = ExtractionSource.PDFPLUMBER_TEXT

        # For hybrid, also try OCR on scanned pages
        if profile.pdf_type == "hybrid":
            try:
                from src.ocr_extractor import extract_with_ocr
                ocr_result = extract_with_ocr(pdf_path)
                # Merge OCR text for scanned pages
                for ocr_page in ocr_result.pages:
                    if not any(p.page_number == ocr_page.page_number and p.text.strip()
                              for p in doc.pages):
                        text += f"\n\n{ocr_page.text}"
                extraction_source = ExtractionSource.PDFPLUMBER_TEXT
            except ImportError:
                logger.warning("  PaddleOCR not installed — scanned pages will be skipped")

    elif profile.pdf_type == "scanned":
        try:
            from src.ocr_extractor import extract_with_ocr
            ocr_result = extract_with_ocr(pdf_path)
            text = ocr_result.full_text
            extraction_source = ExtractionSource.OCR
            logger.info(f"  OCR confidence: {ocr_result.overall_confidence:.1%}")
        except ImportError:
            logger.error(
                "  PaddleOCR not installed. Cannot process scanned PDFs.\n"
                "  Install with: pip install -r requirements-ocr.txt"
            )
            inv = Invoice(source_file=filename, pdf_type=PDFType.SCANNED)
            inv.processing_errors.append("Scanned PDF — OCR not installed")
            return inv

    logger.info(f"  Extracted: {len(text)} chars, {len(tables)} tables")

    # Step 3: Parse into structured Invoice
    inv = parse_invoice(
        text=text,
        tables=tables,
        source_file=filename,
        pdf_type=PDFType(profile.pdf_type),
        extraction_source=extraction_source,
    )
    inv.page_count = profile.page_count

    logger.info(
        f"  Parsed: invoice_num={inv.invoice_number}, "
        f"date={inv.invoice_date}, "
        f"items={len(inv.line_items)}, "
        f"total={inv.grand_total}"
    )

    # Step 4: Validate
    validator = InvoiceValidator()
    inv.validation = validator.validate(inv)
    logger.info(f"  Validation: {inv.validation.status.value}")
    for issue in inv.validation.issues:
        logger.info(f"    [{issue.severity.value}] {issue.field_name}: {issue.issue}")

    return inv


def main():
    parser = argparse.ArgumentParser(
        description="Accuron AI Invoice Parser — Parse invoice PDFs to Excel",
        epilog="Example: python main.py --input Document/ --output output/result.xlsx",
    )
    parser.add_argument(
        "--input", "-i", required=True,
        help="Path to a PDF file or directory containing PDFs",
    )
    parser.add_argument(
        "--output", "-o", default="output/invoice_report.xlsx",
        help="Output Excel file path (default: output/invoice_report.xlsx)",
    )
    parser.add_argument(
        "--no-journal", action="store_true",
        help="Skip Journal Entry format sheet",
    )
    args = parser.parse_args()

    input_path = Path(args.input)
    output_path = args.output

    # Collect PDF files
    if input_path.is_file() and input_path.suffix.lower() == '.pdf':
        pdf_files = [str(input_path)]
    elif input_path.is_dir():
        pdf_files = sorted(str(f) for f in input_path.glob("*.pdf"))
    else:
        logger.error(f"Invalid input: {input_path}")
        sys.exit(1)

    if not pdf_files:
        logger.error(f"No PDF files found in {input_path}")
        sys.exit(1)

    logger.info(f"Found {len(pdf_files)} PDF files to process")
    logger.info(f"Output: {output_path}")

    # Process all invoices with error isolation
    results: list[tuple[str, str, Invoice]] = []
    start_time = time.time()

    for pdf_path in pdf_files:
        try:
            invoice = process_single_invoice(pdf_path)
            results.append(("SUCCESS", pdf_path, invoice))
        except Exception as e:
            logger.error(f"FAILED: {Path(pdf_path).name} — {e}")
            inv = Invoice(source_file=Path(pdf_path).name)
            inv.processing_errors.append(f"Pipeline error: {str(e)}")
            results.append(("FAILED", pdf_path, inv))

    elapsed = time.time() - start_time

    # Deduplication check
    seen_numbers: dict[str, str] = {}
    for status, path, inv in results:
        inv_num = str(inv.invoice_number)
        if inv_num and inv_num != "None" and inv_num in seen_numbers:
            inv.processing_errors.append(
                f"DUPLICATE: Same invoice # found in {seen_numbers[inv_num]}"
            )
            logger.warning(f"  Duplicate invoice #{inv_num}: {Path(path).name} ↔ {seen_numbers[inv_num]}")
        if inv_num and inv_num != "None":
            seen_numbers[inv_num] = Path(path).name

    # Generate Excel output
    invoices = [inv for _, _, inv in results]
    generate_workbook(invoices, output_path, include_journal=not args.no_journal)

    # Print summary
    logger.info(f"\n{'═' * 60}")
    logger.info(f"PROCESSING COMPLETE")
    logger.info(f"{'═' * 60}")
    logger.info(f"Total files:     {len(pdf_files)}")
    logger.info(f"Successful:      {sum(1 for s, _, _ in results if s == 'SUCCESS')}")
    logger.info(f"Failed:          {sum(1 for s, _, _ in results if s == 'FAILED')}")
    logger.info(f"Time elapsed:    {elapsed:.1f}s")
    logger.info(f"Output saved:    {output_path}")

    # Summary table
    logger.info(f"\n{'File':<50} {'Type':<10} {'Invoice #':<20} {'Total':>15} {'Status':<8}")
    logger.info(f"{'─' * 103}")
    for status, path, inv in results:
        fname = Path(path).name[:48]
        pdf_type = inv.pdf_type.value[:8]
        inv_num = str(inv.invoice_number)[:18]
        total = f"₹{inv.grand_total_float:,.2f}" if inv.grand_total_float else "—"
        val_status = inv.validation.status.value if inv.validation else status
        logger.info(f"{fname:<50} {pdf_type:<10} {inv_num:<20} {total:>15} {val_status:<8}")


if __name__ == "__main__":
    main()
