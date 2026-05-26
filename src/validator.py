"""
validator.py — Invoice Validation Layer

Validates extracted invoice data with:
1. GSTIN format validation (15-char Indian GST number)
2. Mathematical cross-checks (line items ≈ subtotal, subtotal + tax ≈ grand total)
3. Date sanity (parseable, not in future, not too old)
4. Completeness check (required fields present)
"""

import re
import logging
from datetime import datetime, timedelta
from dateutil import parser as dateutil_parser

from src.models import (
    Invoice, ValidationResult, ValidationIssue, ValidationStatus,
)

logger = logging.getLogger(__name__)

MATH_TOLERANCE = 2.0  # ₹2 tolerance for rounding differences


class InvoiceValidator:
    """Validates an Invoice object and returns a ValidationResult."""

    def validate(self, invoice: Invoice) -> ValidationResult:
        result = ValidationResult()
        issues = []

        # 1. GSTIN Format Validation
        self._check_gstin(invoice.seller.gstin, "Seller GSTIN", issues)
        self._check_gstin(invoice.buyer.gstin, "Buyer GSTIN", issues)
        result.gst_valid = not any(
            i.field_name in ("Seller GSTIN", "Buyer GSTIN") and i.severity == ValidationStatus.FAIL
            for i in issues
        )

        # 2. Date Validation
        self._check_date(str(invoice.invoice_date), "Invoice Date", issues)

        # 3. Mathematical Cross-Checks
        result.math_check = self._check_math(invoice, issues)

        # 4. Completeness Check
        result.complete = self._check_completeness(invoice, issues)

        # Determine overall status
        result.issues = issues
        fail_count = sum(1 for i in issues if i.severity == ValidationStatus.FAIL)
        warn_count = sum(1 for i in issues if i.severity == ValidationStatus.WARNING)

        if fail_count > 0:
            result.status = ValidationStatus.FAIL
        elif warn_count > 0:
            result.status = ValidationStatus.WARNING
        else:
            result.status = ValidationStatus.PASS

        logger.info(
            f"Validation {invoice.source_file}: {result.status.value} "
            f"({fail_count} errors, {warn_count} warnings)"
        )
        return result

    def _check_gstin(self, gstin: str, field_name: str,
                     issues: list[ValidationIssue]):
        """
        Validate GSTIN format.
        Format: 2 digits + 5 alpha + 4 digits + 1 alpha + 1 digit + Z + 1 alphanum
        Example: 33AABCR7106G1ZQ
        """
        if not gstin:
            issues.append(ValidationIssue(
                field_name=field_name,
                issue="GSTIN not found",
                severity=ValidationStatus.WARNING,
            ))
            return

        gstin = gstin.upper().strip()
        pattern = r'^\d{2}[A-Z]{5}\d{4}[A-Z]\d[A-Z][A-Z\d]$'
        if not re.match(pattern, gstin):
            issues.append(ValidationIssue(
                field_name=field_name,
                issue=f"Invalid GSTIN format: '{gstin}' (expected 15-char pattern)",
                severity=ValidationStatus.WARNING,
                expected="NN AAAAA NNNN A N Z X",
                actual=gstin,
            ))

    def _check_date(self, date_str: str, field_name: str,
                    issues: list[ValidationIssue]):
        """Check date is parseable and reasonable."""
        if not date_str or date_str == "None":
            issues.append(ValidationIssue(
                field_name=field_name,
                issue="Date not found",
                severity=ValidationStatus.WARNING,
            ))
            return

        try:
            parsed = dateutil_parser.parse(date_str, dayfirst=True, fuzzy=True)
            now = datetime.now()

            # Not in future (allow 1 day for timezone differences)
            if parsed > now + timedelta(days=1):
                issues.append(ValidationIssue(
                    field_name=field_name,
                    issue=f"Date is in the future: {date_str}",
                    severity=ValidationStatus.WARNING,
                    actual=date_str,
                ))

            # Not too old (> 5 years)
            if parsed < now - timedelta(days=365 * 5):
                issues.append(ValidationIssue(
                    field_name=field_name,
                    issue=f"Date is more than 5 years old: {date_str}",
                    severity=ValidationStatus.WARNING,
                    actual=date_str,
                ))
        except Exception:
            issues.append(ValidationIssue(
                field_name=field_name,
                issue=f"Cannot parse date: '{date_str}'",
                severity=ValidationStatus.WARNING,
                actual=date_str,
            ))

    def _check_math(self, invoice: Invoice, issues: list[ValidationIssue]) -> bool:
        """
        Mathematical cross-checks:
        - sum(line_item.taxable_amount) ≈ subtotal
        - subtotal + total_tax ≈ grand_total
        - For each item: quantity × unit_price ≈ taxable_amount
        """
        all_ok = True

        # Check line item math: qty × rate ≈ amount
        for i, item in enumerate(invoice.line_items, 1):
            if item.quantity > 0 and item.unit_price > 0 and item.taxable_amount > 0:
                expected = item.quantity * item.unit_price
                if abs(expected - item.taxable_amount) > MATH_TOLERANCE:
                    issues.append(ValidationIssue(
                        field_name=f"Line Item {i}",
                        issue=f"qty({item.quantity}) × rate({item.unit_price}) = "
                              f"{expected:.2f} ≠ amount({item.taxable_amount})",
                        severity=ValidationStatus.WARNING,
                        expected=f"{expected:.2f}",
                        actual=f"{item.taxable_amount:.2f}",
                    ))
                    all_ok = False

        # Check subtotal ≈ sum of line items
        if invoice.line_items and invoice.subtotal > 0:
            items_sum = sum(item.taxable_amount for item in invoice.line_items)
            if abs(items_sum - invoice.subtotal) > MATH_TOLERANCE:
                issues.append(ValidationIssue(
                    field_name="Subtotal",
                    issue=f"Sum of items ({items_sum:.2f}) ≠ subtotal ({invoice.subtotal:.2f})",
                    severity=ValidationStatus.WARNING,
                    expected=f"{items_sum:.2f}",
                    actual=f"{invoice.subtotal:.2f}",
                ))
                all_ok = False

        # Check grand total ≈ subtotal + tax
        grand_total = invoice.grand_total_float
        if grand_total > 0 and invoice.total_tax > 0:
            computed_base = grand_total - invoice.total_tax - invoice.round_off
            if invoice.subtotal > 0:
                if abs(computed_base - invoice.subtotal) > MATH_TOLERANCE:
                    issues.append(ValidationIssue(
                        field_name="Grand Total",
                        issue=f"subtotal({invoice.subtotal:.2f}) + tax({invoice.total_tax:.2f}) "
                              f"= {invoice.subtotal + invoice.total_tax:.2f} ≠ "
                              f"grand_total({grand_total:.2f})",
                        severity=ValidationStatus.WARNING,
                        expected=f"{invoice.subtotal + invoice.total_tax:.2f}",
                        actual=f"{grand_total:.2f}",
                    ))
                    all_ok = False

        return all_ok

    def _check_completeness(self, invoice: Invoice,
                            issues: list[ValidationIssue]) -> bool:
        """Check all required fields are present."""
        complete = True

        required = [
            ("Invoice Number", str(invoice.invoice_number)),
            ("Invoice Date", str(invoice.invoice_date)),
            ("Grand Total", str(invoice.grand_total)),
            ("Seller Name", invoice.seller.name),
        ]

        for name, value in required:
            if not value or value == "None":
                issues.append(ValidationIssue(
                    field_name=name,
                    issue=f"Required field '{name}' is missing",
                    severity=ValidationStatus.FAIL,
                ))
                complete = False

        if not invoice.line_items:
            issues.append(ValidationIssue(
                field_name="Line Items",
                issue="No line items extracted",
                severity=ValidationStatus.WARNING,
            ))

        return complete
