"""
invoice_parser.py — Regex-Based Invoice Field Extraction

Calibrated against actual Accuron AI invoices:
- AWS: "Invoice Number: AIN2526001124876", "July 2, 2025"
- CIEL: "Invoice No. IHR030932627", "28/04/2026", HSN/SAC table
- INUBE: "Invoice No. 25-26/463", "11-Feb-26"
- Green Clean: "GC/26-27/251", "17-Apr-26", 21 line items borderless
- Vault Infosec: "# : VIIPL-2627-002", "02/04/2026"

Each field has 8-10 regex aliases to handle different invoice formats.
dateutil.parser is used as a fallback for date parsing.
"""

import re
import logging
from typing import Optional
from dateutil import parser as dateutil_parser

from src.models import (
    Invoice, ExtractedField, ExtractionSource, PDFType,
    PartyInfo, LineItem, TaxDetail, BankDetails,
)

logger = logging.getLogger(__name__)


# ─── Regex Patterns (8-10 aliases per field) ──────────────────────────────────

INVOICE_NUMBER_PATTERNS = [
    # "Invoice No. IHR030932627" or "Invoice No: 25-26/463"
    r'(?:Invoice\s*(?:No|Number|Num)\.?\s*[:\-]?\s*)([A-Za-z0-9\-/]+)',
    # "Bill No. XXX"
    r'(?:Bill\s*(?:No|Number)\.?\s*[:\-]?\s*)([A-Za-z0-9\-/]+)',
    # "Ref No. XXX"
    r'(?:Ref\s*(?:No|Number)\.?\s*[:\-]?\s*)([A-Za-z0-9\-/]+)',
    # "Invoice Number: AIN2526001124876"
    r'(?:Invoice\s*Number)\s*[:\-]\s*([A-Za-z0-9\-/]+)',
    # "# : VIIPL-2627-002" (Vault style — must have colon)
    r'(?:#\s*:\s*)([A-Za-z0-9\-]+)',
    # "Inv No. XXX"
    r'(?:Inv\.?\s*No\.?\s*[:\-]?\s*)([A-Za-z0-9\-/]+)',
    # "Voucher No. XXX"
    r'(?:Voucher\s*No\.?\s*[:\-]?\s*)([A-Za-z0-9\-/]+)',
    # "Receipt No. XXX"
    r'(?:Receipt\s*No\.?\s*[:\-]?\s*)([A-Za-z0-9\-/]+)',
]

DATE_PATTERNS = [
    # DD/MM/YYYY or DD-MM-YYYY or DD.MM.YYYY
    r'(\d{1,2}[\-/\.]\d{1,2}[\-/\.]\d{2,4})',
    # DD Mon YYYY or DD-Mon-YYYY (e.g., "11-Feb-26", "17-Apr-26")
    r'(\d{1,2}[\s\-]*(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*[\s\-,]*\d{2,4})',
    # Month DD, YYYY (e.g., "July 2, 2025", "Apr 28, 2026")
    r'((?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\s+\d{1,2},?\s*\d{2,4})',
    # YYYY-MM-DD (ISO)
    r'(\d{4}[\-/]\d{1,2}[\-/]\d{1,2})',
]

DATE_LABEL_PATTERNS = [
    r'(?:Invoice\s*)?Date\s*[:\-]?\s*',
    r'Dated\s*[:\-]?\s*',
    r'Inv\.?\s*Date\s*[:\-]?\s*',
    r'Bill\s*Date\s*[:\-]?\s*',
]

GSTIN_PATTERN = r'(\d{2}[A-Za-z]{5}\d{4}[A-Za-z]\d[A-Za-z][A-Za-z\d])'

TOTAL_PATTERNS = [
    # "TOTAL AMOUNT DUE ON July Rs. 2,712,845.83" (AWS style)
    r'TOTAL\s*AMOUNT\s*DUE\s*(?:ON\s*\w+)?\s*(?:Rs\.?|₹|INR)\s*([\d,]+\.?\d*)',
    # "Grand Total 1,77,000.00" or "Grand Total: ₹1,77,000.00"
    r'Grand\s*Total\s*[:\-]?\s*(?:Rs\.?|₹|INR)?\s*([\d,]+\.?\d*)',
    # "Total Invoice Value" or "Total Amount"
    r'(?:Total\s*Invoice\s*Value|Total\s*Amount)\s*[:\-]?\s*(?:Rs\.?|₹|INR)?\s*([\d,]+\.?\d*)',
    # "Net Amount" / "Net Payable" / "Amount Payable" / "Balance Due"
    r'(?:Net\s*(?:Amount|Payable)|Amount\s*Payable|Balance\s*Due|Total\s*Due)\s*[:\-]?\s*(?:Rs\.?|₹|INR)?\s*([\d,]+\.?\d*)',
    # "Total Rs. 21,24,000.00" (INUBE style — Total with currency symbol)
    r'^Total\s+(?:Rs\.?|₹|INR)\s*([\d,]+\.\d{2})\s*$',
    # Simple "Total 12,90,774.86" standalone (CIEL style)
    r'^Total\s+(\d[\d,]+\.\d{2})\s*$',
]


SUBTOTAL_PATTERNS = [
    r'(?:Sub\s*Total|Subtotal|Taxable\s*Value|Net\s*Charges)\s*[:\-]?\s*(?:Rs\.?|₹|INR)?\s*([\d,]+\.?\d*)',
]

PLACE_OF_SUPPLY_PATTERNS = [
    r'Place\s*(?:Of|of)\s*Supply\s*[:\-]?\s*(.+?)(?:\n|\(|$)',
]

IRN_PATTERNS = [
    r'IRN\s*[:\-]?\s*([a-f0-9]{64})',
    r'IRN\s*[:\-]?\s*([A-Za-z0-9]{30,})',
]

ACK_NO_PATTERNS = [
    r'Ack\s*(?:No|Number)\.?\s*[:\-]?\s*(\d+)',
]

ACK_DATE_PATTERNS = [
    r'Ack\s*Date\s*[:\-]?\s*(.+?)(?:\n|$)',
]

PO_NUMBER_PATTERNS = [
    r'(?:P\.?O\.?\s*(?:No|#|Number)?|Purchase\s*Order\s*(?:No)?|Buyer.?s\s*Order\s*No)\.?\s*[:\-]?\s*([A-Za-z0-9\-/]+(?:\s*[A-Za-z0-9\-/]+)*)',
]

AMOUNT_IN_WORDS_PATTERNS = [
    r'(?:Amount\s*(?:Chargeable|in\s*Words?))\s*(?:\(in\s*words\))?\s*[:\-]?\s*(?:E\.\s*&?\s*O\.E\.?)?\s*\n?\s*(?:INR|Rs\.?|Indian\s*Rupee?s?)?\s*(.*?)(?:\n|$)',
    r'(?:Total\s*In\s*Words)\s*\n?\s*(?:INR|Rs\.?|Indian\s*Rupee?s?)?\s*(.*?)(?:\n|$)',
]

BANK_NAME_PATTERN = r'Bank\s*Name\s*[:\-]?\s*(.+?)(?:\n|$)'
BANK_ACCOUNT_PATTERN = r'A/?c\s*No\.?\s*[:\-]?\s*(\d+)'
BANK_IFSC_PATTERN = r'(?:IFS?\s*Code|IFSC)\s*[:\-]?\s*([A-Z]{4}\d{7})'
BANK_BRANCH_PATTERN = r'Branch\s*(?:&?\s*IFS?\s*Code)?\s*[:\-]?\s*(.+?)(?:\n|$)'
SWIFT_PATTERN = r'SWIFT\s*(?:Code)?\s*[:\-]?\s*([A-Z]+)'

HSN_PATTERN = r'(?:HSN|SAC|HSN/SAC)\s*[:\-]?\s*(\d{4,8})'


# ─── Helper Functions ─────────────────────────────────────────────────────────

def _search_patterns(text: str, patterns: list[str], flags=re.IGNORECASE | re.MULTILINE) -> Optional[str]:
    """Try multiple regex patterns, return first match."""
    for pattern in patterns:
        match = re.search(pattern, text, flags)
        if match:
            return match.group(1).strip()
    return None


def _search_all(text: str, pattern: str, flags=re.IGNORECASE) -> list[str]:
    """Find all matches for a pattern."""
    return [m.strip() for m in re.findall(pattern, text, flags)]


def _parse_amount(text: str) -> float:
    """
    Parse an amount string to float.
    Handles Indian lakh format (1,77,000.00), ₹, Rs., commas.
    
    Indian number format: 1,23,45,678.90
    Western format: 123,456,789.90
    Both become: 12345678.90
    """
    if not text:
        return 0.0
    # Remove currency symbols and spaces
    cleaned = re.sub(r'[₹\s]', '', text)
    cleaned = re.sub(r'^Rs\.?\s*', '', cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r'^INR\s*', '', cleaned, flags=re.IGNORECASE)
    # Remove all commas (handles both Indian and Western formats)
    cleaned = cleaned.replace(',', '')
    # Remove any trailing non-numeric chars
    cleaned = re.sub(r'[^\d.\-]', '', cleaned)
    try:
        return float(cleaned)
    except ValueError:
        return 0.0


def _parse_date(text: str) -> Optional[str]:
    """
    Parse a date string to normalized DD/MM/YYYY format.
    Uses dateutil as fallback for any format.
    """
    if not text:
        return None
    try:
        # Try dateutil (handles almost everything)
        parsed = dateutil_parser.parse(text, dayfirst=True, fuzzy=True)
        return parsed.strftime("%d/%m/%Y")
    except Exception:
        return text.strip()


def _extract_invoice_date(text: str) -> Optional[str]:
    """
    Extract invoice date using labeled patterns first, then raw date patterns.
    
    Handles formats seen in Accuron invoices:
    - "28/04/2026" (CIEL)
    - "11-Feb-26" (INUBE) 
    - "July 2, 2025" (AWS)
    - "02/04/2026" (Vault)
    - "17-Apr-26" (Green Clean)
    """
    # Try labeled patterns (most specific)
    for label in DATE_LABEL_PATTERNS:
        for date_pat in DATE_PATTERNS:
            combined = label + r'\s*' + date_pat
            match = re.search(combined, text, re.IGNORECASE)
            if match:
                return _parse_date(match.group(1))

    # Fallback: find date-like string on any line containing "date" or "dated"
    lines = text.split('\n')
    for line in lines:
        if re.search(r'\bdate[d]?\b', line, re.IGNORECASE):
            for pat in DATE_PATTERNS:
                m = re.search(pat, line, re.IGNORECASE)
                if m:
                    return _parse_date(m.group(1))

    # Last resort: look for "Dated" followed by date on same or next line  
    dated_match = re.search(r'Dated\s*\n?\s*(' + '|'.join(p[1:-1] for p in DATE_PATTERNS) + ')', text, re.IGNORECASE)
    if dated_match:
        return _parse_date(dated_match.group(1))

    return None


def _extract_gstins(text: str) -> list[str]:
    """Extract all GSTIN numbers from text."""
    matches = re.findall(GSTIN_PATTERN, text, re.IGNORECASE)
    # Normalize: uppercase, remove spaces
    normalized = list(set(m.upper().replace(" ", "") for m in matches))
    return normalized


def _extract_party_block(text: str, start_keywords: list[str],
                          end_keywords: list[str]) -> str:
    """Extract a text block between start and end keywords."""
    for start_kw in start_keywords:
        pattern = re.compile(
            rf'{start_kw}\s*[:\-]?\s*\n?(.*?)(?:{"|".join(end_keywords)}|\Z)',
            re.IGNORECASE | re.DOTALL
        )
        match = pattern.search(text)
        if match:
            return match.group(1).strip()
    return ""


def _parse_party_info(block: str) -> PartyInfo:
    """Parse a text block into PartyInfo."""
    info = PartyInfo()
    if not block:
        return info

    lines = [l.strip() for l in block.split('\n') if l.strip()]
    if lines:
        # First non-empty line is usually the name
        info.name = lines[0]

    # GSTIN
    gstin_match = re.search(GSTIN_PATTERN, block, re.IGNORECASE)
    if gstin_match:
        info.gstin = gstin_match.group(1).upper()

    # State
    state_match = re.search(r'State\s*(?:Name)?\s*[:\-]?\s*(.+?)(?:,|Code|\n|$)', block, re.IGNORECASE)
    if state_match:
        info.state = state_match.group(1).strip()

    # State Code
    code_match = re.search(r'Code\s*[:\-]?\s*(\d{1,2})', block, re.IGNORECASE)
    if code_match:
        info.state_code = code_match.group(1)

    # PAN
    pan_match = re.search(r'PAN\s*(?:No)?\.?\s*[:\-]?\s*([A-Z]{5}\d{4}[A-Z])', block, re.IGNORECASE)
    if pan_match:
        info.pan = pan_match.group(1).upper()

    # Email
    email_match = re.search(r'[\w.+-]+@[\w-]+\.[\w.]+', block)
    if email_match:
        info.email = email_match.group(0)

    # Address: everything between name and GSTIN/State/PAN lines
    address_lines = []
    for line in lines[1:]:
        if re.search(r'GSTIN|State|PAN|Code|CIN|Email|Phone|Contact', line, re.IGNORECASE):
            break
        address_lines.append(line)
    info.address = ", ".join(address_lines)

    return info


# ─── Table Parsing ────────────────────────────────────────────────────────────

# Column header keywords for dynamic mapping
COLUMN_KEYWORDS = {
    'sr_no': ['sl', 'sr', 'sno', 's.no', 'sl.no', 'sr.no', '#', 'no.', 'no'],
    'description': ['description', 'particulars', 'item', 'product', 'goods', 'service',
                     'item & description', 'item \u0026 description'],
    'hsn_sac': ['hsn', 'sac', 'hsn/sac', 'hsn code', 'sac code'],
    'quantity': ['qty', 'quantity', 'nos', 'units', 'unit'],
    'rate': ['rate', 'price', 'unit price', 'mrp', 'unit rate'],
    'per': ['per', 'uom'],
    'discount': ['disc', 'discount', 'disc.', 'disc %'],
    'amount': ['amount', 'total', 'value', 'net amount', 'net value'],
    'gst_rate': ['gst', 'gst rate', 'gst %', 'tax rate', 'rate'],
    'cgst': ['cgst', 'cgst amt', 'cgst amount'],
    'sgst': ['sgst', 'sgst amt', 'sgst amount', 'utgst'],
    'igst': ['igst', 'igst amt', 'igst amount'],
}


def _map_columns(header_row: list[str]) -> dict[str, int]:
    """
    Map column indices to field names based on header keywords.
    Returns: {field_name: column_index}
    """
    mapping = {}
    for col_idx, cell in enumerate(header_row):
        if not cell:
            continue
        cell_lower = cell.lower().strip()
        for field_name, keywords in COLUMN_KEYWORDS.items():
            if any(kw in cell_lower for kw in keywords):
                if field_name not in mapping:  # First match wins
                    mapping[field_name] = col_idx
                    break
    return mapping


def _is_header_row(row: list[str]) -> bool:
    """Check if a row looks like a table header."""
    if not row:
        return False
    text = " ".join(str(c or "").lower() for c in row)
    header_keywords = ['description', 'particulars', 'qty', 'quantity', 'rate',
                       'amount', 'hsn', 'sac', 'item', 'sl.no', 'sr.no']
    matches = sum(1 for kw in header_keywords if kw in text)
    return matches >= 2


def _is_total_row(row: list[str]) -> bool:
    """Check if a row is a totals/summary row (not a line item)."""
    text = " ".join(str(c or "").lower() for c in row)
    total_keywords = ['total', 'sub total', 'subtotal', 'grand total',
                      'amount chargeable', 'tax amount', 'carried forward',
                      'continued', 'output', 'cgst', 'sgst', 'igst',
                      'round off', 'e. & o.e']
    return any(kw in text for kw in total_keywords)


def parse_line_items_from_table(table: list[list[str]]) -> list[LineItem]:
    """
    Parse a 2D table into LineItem objects.
    Dynamically maps columns based on header keywords.
    """
    if not table or len(table) < 2:
        return []

    # Find header row
    header_idx = -1
    for i, row in enumerate(table):
        if _is_header_row(row):
            header_idx = i
            break

    if header_idx < 0:
        logger.warning("No header row found in table")
        return []

    col_map = _map_columns(table[header_idx])
    if not col_map:
        logger.warning("Could not map any columns from header")
        return []

    logger.debug(f"Column mapping: {col_map}")

    items = []
    for row_idx in range(header_idx + 1, len(table)):
        row = table[row_idx]

        # Skip total/summary rows
        if _is_total_row(row):
            continue

        # Skip empty rows
        if not any(str(c or "").strip() for c in row):
            continue

        item = LineItem()

        # Sr No
        if 'sr_no' in col_map:
            try:
                val = str(row[col_map['sr_no']] or "").strip().rstrip('.')
                item.sr_no = int(float(val)) if val else 0
            except (ValueError, IndexError):
                pass

        # Description
        if 'description' in col_map:
            try:
                val = str(row[col_map['description']] or "").strip()
                # Clean multi-line descriptions
                item.description = re.sub(r'\n+', ' | ', val)
            except IndexError:
                pass

        # Skip rows without description (likely not a real line item)
        if not item.description:
            continue

        # HSN/SAC
        if 'hsn_sac' in col_map:
            try:
                item.hsn_sac = str(row[col_map['hsn_sac']] or "").strip()
            except IndexError:
                pass

        # Quantity
        if 'quantity' in col_map:
            try:
                item.quantity = _parse_amount(str(row[col_map['quantity']] or ""))
            except IndexError:
                pass

        # Unit Price / Rate
        if 'rate' in col_map:
            try:
                val = str(row[col_map['rate']] or "")
                # Handle multi-line values (rate + tax rate in same cell)
                val = val.split('\n')[0]
                item.unit_price = _parse_amount(val)
            except IndexError:
                pass

        # Amount
        if 'amount' in col_map:
            try:
                val = str(row[col_map['amount']] or "")
                val = val.split('\n')[0]  # First value is the amount
                item.taxable_amount = _parse_amount(val)
                item.total_amount = item.taxable_amount
            except IndexError:
                pass

        # GST Rate
        if 'gst_rate' in col_map:
            try:
                val = str(row[col_map['gst_rate']] or "").replace('%', '').strip()
                item.gst_rate = float(val) if val else 0.0
            except (ValueError, IndexError):
                pass

        # CGST/SGST/IGST amounts
        for tax_field, model_field in [('cgst', 'cgst_amount'), ('sgst', 'sgst_amount'), ('igst', 'igst_amount')]:
            if tax_field in col_map:
                try:
                    val = str(row[col_map[tax_field]] or "")
                    setattr(item, model_field, _parse_amount(val))
                except IndexError:
                    pass

        items.append(item)

    return items


# ─── Text-Based Line Item Extraction ──────────────────────────────────────────

def parse_line_items_from_text(text: str) -> list[LineItem]:
    """
    Extract line items from raw text when no table is available.
    Uses pattern matching on common invoice line formats.
    
    Handles formats like Green Clean:
    "1 SCORTCH BRITE SMALL 73231000 18% 2 NOS 10.00 NOS 20.00"
    """
    items = []
    # Pattern for line items with sr_no, description, HSN, GST%, qty, unit, rate, amount
    line_pattern = re.compile(
        r'^\s*(\d+)\s+'           # Sr No
        r'(.+?)\s+'              # Description
        r'(\d{4,8})\s+'          # HSN/SAC
        r'(\d+\.?\d*)\s*%\s+'   # GST Rate%
        r'(\d+\.?\d*)\s+'       # Quantity
        r'(\w+)\s+'              # Unit
        r'([\d,]+\.?\d*)\s+'    # Rate
        r'(?:\w+\s+)?'          # Per (optional)
        r'(?:[\d.]+%?\s+)?'     # Discount (optional)
        r'([\d,]+\.?\d*)\s*$',  # Amount
        re.MULTILINE
    )

    for match in line_pattern.finditer(text):
        item = LineItem(
            sr_no=int(match.group(1)),
            description=match.group(2).strip(),
            hsn_sac=match.group(3),
            gst_rate=float(match.group(4)),
            quantity=float(match.group(5)),
            unit=match.group(6),
            unit_price=_parse_amount(match.group(7)),
            taxable_amount=_parse_amount(match.group(8)),
            total_amount=_parse_amount(match.group(8)),
        )
        items.append(item)

    return items


# ─── Tax Detail Extraction ───────────────────────────────────────────────────

def _extract_tax_details(text: str) -> list[TaxDetail]:
    """Extract CGST/SGST/IGST breakdown from text."""
    details = []

    # Pattern: "CGST 9% 348.39" or "IGST 18% 1,96,897.86"
    tax_patterns = [
        # Table format: "18%  1,96,897.86" with label context
        r'(CGST|SGST|IGST|UTGST|CESS)\s*(?:\d+\s*)?(?:\(?\s*)?(\d+\.?\d*)\s*%?\)?\s*[:\-]?\s*([\d,]+\.?\d*)',
        # "OUTPUT@CGST 354.37" (Green Clean style)
        r'OUTPUT\s*@\s*(CGST|SGST|IGST)\s*([\d,]+\.?\d*)',
        # "CGST9 (9%) 13,500.00" (Vault style)
        r'(CGST|SGST|IGST)\d*\s*\((\d+\.?\d*)\s*%?\)\s*([\d,]+\.?\d*)',
    ]

    for pattern in tax_patterns:
        for match in re.finditer(pattern, text, re.IGNORECASE):
            groups = match.groups()
            if len(groups) == 3:
                details.append(TaxDetail(
                    tax_type=groups[0].upper(),
                    rate=float(groups[1]),
                    tax_amount=_parse_amount(groups[2]),
                ))
            elif len(groups) == 2:
                details.append(TaxDetail(
                    tax_type=groups[0].upper(),
                    rate=0.0,
                    tax_amount=_parse_amount(groups[1]),
                ))

    # Deduplicate by tax_type (keep highest amount)
    seen = {}
    for td in details:
        key = td.tax_type
        if key not in seen or td.tax_amount > seen[key].tax_amount:
            seen[key] = td
    return list(seen.values())


# ─── Bank Details Extraction ──────────────────────────────────────────────────

def _extract_bank_details(text: str) -> BankDetails:
    """Extract bank details from text."""
    bank = BankDetails()

    match = re.search(BANK_NAME_PATTERN, text, re.IGNORECASE)
    if match:
        bank.bank_name = match.group(1).strip()

    match = re.search(BANK_ACCOUNT_PATTERN, text, re.IGNORECASE)
    if match:
        bank.account_number = match.group(1).strip()

    match = re.search(BANK_IFSC_PATTERN, text, re.IGNORECASE)
    if match:
        bank.ifsc_code = match.group(1).strip()

    match = re.search(BANK_BRANCH_PATTERN, text, re.IGNORECASE)
    if match:
        raw = match.group(1).strip()
        # Split "Annasalai & FDRL0001100" → branch + IFSC
        parts = re.split(r'\s*&\s*', raw)
        bank.branch = parts[0].strip() if parts else raw
        if len(parts) > 1 and re.match(r'[A-Z]{4}\d{7}', parts[1].strip()):
            bank.ifsc_code = parts[1].strip()

    match = re.search(SWIFT_PATTERN, text, re.IGNORECASE)
    if match:
        bank.swift_code = match.group(1).strip()

    return bank


# ─── Main Parser ──────────────────────────────────────────────────────────────

def parse_invoice(text: str, tables: list[list[list[str]]],
                  source_file: str = "",
                  pdf_type: PDFType = PDFType.DIGITAL,
                  extraction_source: ExtractionSource = ExtractionSource.PDFPLUMBER_TEXT
                  ) -> Invoice:
    """
    Parse extracted text and tables into a structured Invoice object.
    
    Args:
        text: Full extracted text from all pages
        tables: Any tables extracted (bordered or clustered)
        source_file: Original PDF filename
        pdf_type: Digital or scanned
        extraction_source: How the text was extracted
    
    Returns:
        Invoice object with all extracted fields
    """
    inv = Invoice(
        source_file=source_file,
        pdf_type=pdf_type,
        extraction_method=extraction_source,
        raw_text=text,
    )

    if not text:
        inv.processing_errors.append("No text extracted from PDF")
        return inv

    # ── Invoice Number ─────────────────────────────────────────────────────
    inv_num = _search_patterns(text, INVOICE_NUMBER_PATTERNS)

    # Tally format: "Invoice No. Date\n[possible other lines]\nIHR030932627 28/04/2026"
    # CIEL: "Invoice No. Date\nConsignee (Ship to)\nIHR030932627 28/04/2026"
    # INUBE: "running Invoice No. Dated\n25-26/463 11-Feb-26"
    # Green Clean: "Invoice\nNo, Dated\n...\nGC/26-27/251 17-Apr-26"
    REJECT_WORDS = {
        'consignee', 'royal', 'green', 'clean', 'services', 'dated', 'date',
        'buyer', 'seller', 'ship', 'bill', 'dispatch', 'delivery', 'note',
        'mode', 'terms', 'payment', 'vishranthi', 'towers', 'limited',
        'private', 'pvt', 'ltd', 'chennai', 'bangalore', 'mumbai',
        'sundaram', 'general', 'insurance',
    }
    
    if not inv_num or inv_num.lower() in ('date', 'dated', 'invoice', 'consignee'):
        lines = text.split('\n')
        found_header = False
        
        for li, line in enumerate(lines):
            # Find the "Invoice No" header line
            if re.search(r'Invoice\s*(?:No|Number)\.?', line, re.IGNORECASE):
                found_header = True
                
                # Check if invoice number value is ON the same line after the header label
                # e.g., "running Invoice No. Dated\n25-26/463"
                # But first check the NEXT few lines for the value
                for offset in range(1, 6):
                    if li + offset >= len(lines):
                        break
                    next_line = lines[li + offset].strip()
                    if not next_line:
                        continue
                    
                    # First token on this line — is it an invoice number?
                    first_token = next_line.split()[0] if next_line.split() else ""
                    first_token_clean = first_token.rstrip('.')
                    
                    # An invoice number typically: has at least one digit, 
                    # may contain letters/dashes/slashes, length >= 5
                    if (len(first_token_clean) >= 5 and 
                        re.search(r'\d', first_token_clean) and
                        re.match(r'^[A-Za-z0-9\-/]+$', first_token_clean) and
                        first_token_clean.lower() not in REJECT_WORDS):
                        inv_num = first_token_clean
                        break
                
                if inv_num and inv_num.lower() not in ('date', 'dated', 'invoice', 'consignee'):
                    break


    if inv_num and inv_num.lower() not in ('date', 'dated', 'invoice', 'consignee'):
        inv.invoice_number = ExtractedField.high(inv_num, extraction_source)
    else:
        inv.processing_errors.append("Could not extract invoice number")

    # ── Invoice Date ───────────────────────────────────────────────────────
    date_str = _extract_invoice_date(text)

    # Tally format fallback: date is on same line as invoice number value
    if not date_str and inv_num:
        # Find the line containing the invoice number and grab date from it
        for line in text.split('\n'):
            if inv_num in line:
                for pat in DATE_PATTERNS:
                    m = re.search(pat, line, re.IGNORECASE)
                    if m:
                        date_str = _parse_date(m.group(1))
                        break
                break

    if date_str:

        inv.invoice_date = ExtractedField.high(date_str, extraction_source)
    else:
        inv.processing_errors.append("Could not extract invoice date")

    # ── e-Invoice Fields ───────────────────────────────────────────────────
    inv.irn = _search_patterns(text, IRN_PATTERNS) or ""
    inv.ack_number = _search_patterns(text, ACK_NO_PATTERNS) or ""
    inv.ack_date = _search_patterns(text, ACK_DATE_PATTERNS) or ""
    inv.po_number = _search_patterns(text, PO_NUMBER_PATTERNS) or ""

    # ── Place of Supply ────────────────────────────────────────────────────
    inv.place_of_supply = _search_patterns(text, PLACE_OF_SUPPLY_PATTERNS) or ""

    # ── GSTINs ─────────────────────────────────────────────────────────────
    gstins = _extract_gstins(text)

    # ── Seller (first block of text, usually top) ──────────────────────────
    seller_block = _extract_party_block(text,
        start_keywords=[r'^', r'Tax Invoice', r'GST Invoice'],
        end_keywords=[r'Consignee', r'Bill\s*To', r'Buyer', r'Issued\s*To',
                       r'Ship\s*To', r'Sold\s*To', r'Invoice\s*No']
    )
    inv.seller = _parse_party_info(seller_block)

    # ── Buyer ──────────────────────────────────────────────────────────────
    buyer_block = _extract_party_block(text,
        start_keywords=[r'Bill\s*(?:To|to)', r'Buyer\s*\(Bill\s*to\)', r'Buyer',
                        r'Issued\s*To', r'Sold\s*To'],
        end_keywords=[r'Sl\.?\s*No', r'Sr\.?\s*No', r'#\s', r'Particulars',
                       r'Description', r'Item', r'HSN', r'Dispatch']
    )
    inv.buyer = _parse_party_info(buyer_block)

    # ── Ship To ────────────────────────────────────────────────────────────
    ship_block = _extract_party_block(text,
        start_keywords=[r'Ship\s*To', r'Consignee\s*\(Ship\s*to\)', r'Consignee'],
        end_keywords=[r'Bill\s*To', r'Buyer', r'Invoice\s*No', r'Sl\.?\s*No',
                       r'Dispatch']
    )
    if ship_block:
        inv.ship_to = _parse_party_info(ship_block)

    # Assign GSTINs to parties if not already found
    for gstin in gstins:
        if not inv.seller.gstin and gstin != inv.buyer.gstin:
            inv.seller.gstin = gstin
        elif not inv.buyer.gstin and gstin != inv.seller.gstin:
            inv.buyer.gstin = gstin

    # ── Line Items ─────────────────────────────────────────────────────────
    # Try from tables first
    for table in tables:
        items = parse_line_items_from_table(table)
        if items:
            inv.line_items.extend(items)

    # If no items from tables, try text-based extraction
    if not inv.line_items:
        inv.line_items = parse_line_items_from_text(text)

    if not inv.line_items:
        inv.processing_errors.append("Could not extract any line items")

    # ── Tax Details ────────────────────────────────────────────────────────
    inv.tax_details = _extract_tax_details(text)

    # ── Totals ─────────────────────────────────────────────────────────────
    subtotal_str = _search_patterns(text, SUBTOTAL_PATTERNS)
    if subtotal_str:
        inv.subtotal = _parse_amount(subtotal_str)

    total_str = _search_patterns(text, TOTAL_PATTERNS)
    if total_str:
        inv.grand_total = ExtractedField.high(total_str, extraction_source)
    else:
        # Try to compute from line items
        if inv.line_items:
            computed = sum(item.taxable_amount for item in inv.line_items)
            inv.grand_total = ExtractedField.medium(f"{computed:.2f}", ExtractionSource.INFERRED)

    # Total tax
    if inv.tax_details:
        inv.total_tax = sum(td.tax_amount for td in inv.tax_details)

    # Amount in words
    inv.amount_in_words = _search_patterns(text, AMOUNT_IN_WORDS_PATTERNS) or ""

    # Round off
    round_match = re.search(r'Round\s*Off\s*[:\-]?\s*([\-\d.]+)', text, re.IGNORECASE)
    if round_match:
        try:
            inv.round_off = float(round_match.group(1))
        except ValueError:
            pass

    # ── Bank Details ───────────────────────────────────────────────────────
    inv.bank = _extract_bank_details(text)

    # ── Reverse Charge ─────────────────────────────────────────────────────
    rc_match = re.search(r'Reverse\s*Charge\s*[:\-]?\s*(\w+)', text, re.IGNORECASE)
    if rc_match:
        inv.reverse_charge = rc_match.group(1).strip()

    # ── Page count ─────────────────────────────────────────────────────────
    page_markers = text.count('--- PAGE BREAK ---')
    inv.page_count = page_markers + 1 if page_markers else 1

    logger.info(
        f"Parsed {source_file}: invoice_num={inv.invoice_number}, "
        f"date={inv.invoice_date}, items={len(inv.line_items)}, "
        f"total={inv.grand_total}"
    )
    return inv
