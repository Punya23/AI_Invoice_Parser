"""
models.py — Data models for Accuron AI Invoice Parser

Pragmatic design for POC:
- Plain Python dataclasses with simple types for fast development
- Field-level confidence only on critical fields (invoice_number, date, totals, GSTIN)
- Everything else is plain str/float for speed
"""

from dataclasses import dataclass, field
from typing import Optional
from enum import Enum


# ─── Enums ────────────────────────────────────────────────────────────────────

class PDFType(str, Enum):
    DIGITAL = "digital"
    SCANNED = "scanned"
    HYBRID  = "hybrid"
    CORRUPT = "corrupt"


class ExtractionSource(str, Enum):
    PDFPLUMBER_TABLE   = "pdfplumber_table"
    PDFPLUMBER_TEXT    = "pdfplumber_text"
    POSITIONAL_CLUSTER = "positional_cluster"
    OCR                = "ocr"
    INFERRED           = "inferred"


class ValidationStatus(str, Enum):
    PASS    = "PASS"
    WARNING = "WARNING"
    FAIL    = "FAIL"


# ─── Field-Level Confidence (for critical fields only) ───────────────────────

@dataclass
class ExtractedField:
    """Wraps a value with confidence and source provenance."""
    value: Optional[str]
    confidence: float = 0.0
    source: ExtractionSource = ExtractionSource.PDFPLUMBER_TEXT
    page_number: int = 1
    needs_review: bool = False

    def __post_init__(self):
        self.needs_review = self.confidence < 0.75

    @classmethod
    def empty(cls) -> "ExtractedField":
        return cls(value=None, confidence=0.0, needs_review=True)

    @classmethod
    def high(cls, value: str, source: ExtractionSource = ExtractionSource.PDFPLUMBER_TEXT,
             page: int = 1) -> "ExtractedField":
        return cls(value=value, confidence=0.95, source=source, page_number=page)

    @classmethod
    def medium(cls, value: str, source: ExtractionSource = ExtractionSource.PDFPLUMBER_TEXT,
               page: int = 1) -> "ExtractedField":
        return cls(value=value, confidence=0.75, source=source, page_number=page)

    def __str__(self):
        return str(self.value) if self.value else ""


# ─── Sub-Models (plain types for speed) ───────────────────────────────────────

@dataclass
class PartyInfo:
    name: str = ""
    address: str = ""
    gstin: str = ""
    state: str = ""
    state_code: str = ""
    pan: str = ""
    email: str = ""
    phone: str = ""


@dataclass
class LineItem:
    sr_no: int = 0
    description: str = ""
    hsn_sac: str = ""
    quantity: float = 0.0
    unit: str = ""
    unit_price: float = 0.0
    discount: float = 0.0
    taxable_amount: float = 0.0
    gst_rate: float = 0.0
    cgst_amount: float = 0.0
    sgst_amount: float = 0.0
    igst_amount: float = 0.0
    total_amount: float = 0.0


@dataclass
class TaxDetail:
    tax_type: str = ""      # CGST / SGST / IGST / CESS
    rate: float = 0.0
    taxable_amount: float = 0.0
    tax_amount: float = 0.0


@dataclass
class BankDetails:
    bank_name: str = ""
    account_number: str = ""
    ifsc_code: str = ""
    branch: str = ""
    swift_code: str = ""


@dataclass
class ValidationIssue:
    field_name: str
    issue: str
    severity: ValidationStatus
    expected: Optional[str] = None
    actual: Optional[str] = None


@dataclass
class ValidationResult:
    status: ValidationStatus = ValidationStatus.PASS
    issues: list[ValidationIssue] = field(default_factory=list)
    math_check: bool = False
    gst_valid: bool = False
    complete: bool = False


# ─── Top-Level Invoice ────────────────────────────────────────────────────────

@dataclass
class Invoice:
    # --- Source Metadata ---
    source_file: str = ""
    pdf_type: PDFType = PDFType.DIGITAL
    extraction_method: ExtractionSource = ExtractionSource.PDFPLUMBER_TEXT
    page_count: int = 1

    # --- Critical Fields (with confidence tracking) ---
    invoice_number: ExtractedField = field(default_factory=ExtractedField.empty)
    invoice_date: ExtractedField = field(default_factory=ExtractedField.empty)
    grand_total: ExtractedField = field(default_factory=ExtractedField.empty)

    # --- Header Fields (plain for speed) ---
    due_date: str = ""
    po_number: str = ""
    place_of_supply: str = ""
    reverse_charge: str = ""
    invoice_type: str = "Tax Invoice"

    # --- e-Invoice Fields ---
    irn: str = ""
    ack_number: str = ""
    ack_date: str = ""

    # --- Parties ---
    seller: PartyInfo = field(default_factory=PartyInfo)
    buyer: PartyInfo = field(default_factory=PartyInfo)
    ship_to: Optional[PartyInfo] = None

    # --- Line Items ---
    line_items: list[LineItem] = field(default_factory=list)

    # --- Tax ---
    tax_details: list[TaxDetail] = field(default_factory=list)
    subtotal: float = 0.0
    total_discount: float = 0.0
    total_tax: float = 0.0
    round_off: float = 0.0
    amount_in_words: str = ""

    # --- Bank Details ---
    bank: BankDetails = field(default_factory=BankDetails)

    # --- Notes ---
    terms: str = ""
    notes: str = ""

    # --- Validation ---
    validation: Optional[ValidationResult] = None
    processing_errors: list[str] = field(default_factory=list)
    raw_text: str = ""

    @property
    def grand_total_float(self) -> float:
        """Parse grand_total ExtractedField to float."""
        try:
            val = str(self.grand_total.value or "0")
            val = val.replace(",", "").replace("₹", "").replace("Rs.", "").replace("Rs", "").strip()
            return float(val)
        except (ValueError, TypeError):
            return 0.0

    @property
    def overall_confidence(self) -> float:
        """Weighted confidence of critical fields."""
        fields = [
            (self.invoice_number.confidence, 2.0),
            (self.invoice_date.confidence, 2.0),
            (self.grand_total.confidence, 2.0),
        ]
        total_w = sum(w for _, w in fields)
        return round(sum(c * w for c, w in fields) / total_w, 3) if total_w else 0.0

    @property
    def is_reliable(self) -> bool:
        return self.overall_confidence >= 0.75
