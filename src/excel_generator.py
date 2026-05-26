"""
excel_generator.py — Professional Excel Output

Generates two types of output:
1. Invoice Summary Workbook — human-readable multi-sheet workbook
   - Summary dashboard (all invoices)
   - Individual invoice detail tabs
   - Extraction log

2. Journal Entry Upload — matches Accuron's specific ERP upload format
   (56-column Journal Entry format from their template)
"""

import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

import openpyxl
from openpyxl.styles import (
    Font, PatternFill, Border, Side, Alignment, numbers,
)
from openpyxl.utils import get_column_letter

from src.models import Invoice, ValidationStatus

logger = logging.getLogger(__name__)

# ─── Styling Constants ────────────────────────────────────────────────────────

HEADER_FONT = Font(name='Calibri', bold=True, color='FFFFFF', size=11)
HEADER_FILL = PatternFill(start_color='1F4E79', end_color='1F4E79', fill_type='solid')
SUBHEADER_FILL = PatternFill(start_color='D6E4F0', end_color='D6E4F0', fill_type='solid')
ALT_ROW_FILL = PatternFill(start_color='F2F2F2', end_color='F2F2F2', fill_type='solid')
PASS_FILL = PatternFill(start_color='C6EFCE', end_color='C6EFCE', fill_type='solid')
WARN_FILL = PatternFill(start_color='FFEB9C', end_color='FFEB9C', fill_type='solid')
FAIL_FILL = PatternFill(start_color='FFC7CE', end_color='FFC7CE', fill_type='solid')
THIN_BORDER = Border(
    left=Side(style='thin'), right=Side(style='thin'),
    top=Side(style='thin'), bottom=Side(style='thin'),
)
CURRENCY_FORMAT = '#,##0.00'
BOLD_FONT = Font(name='Calibri', bold=True, size=11)
NORMAL_FONT = Font(name='Calibri', size=11)


def _auto_width(ws, min_width=8, max_width=50):
    """Auto-adjust column widths based on content."""
    for col in ws.columns:
        max_len = min_width
        col_letter = get_column_letter(col[0].column)
        for cell in col:
            if cell.value:
                cell_len = len(str(cell.value))
                max_len = max(max_len, min(cell_len + 2, max_width))
        ws.column_dimensions[col_letter].width = max_len


def _style_header_row(ws, row_num, col_count):
    """Apply header styling to a row."""
    for col in range(1, col_count + 1):
        cell = ws.cell(row=row_num, column=col)
        cell.font = HEADER_FONT
        cell.fill = HEADER_FILL
        cell.border = THIN_BORDER
        cell.alignment = Alignment(horizontal='center', vertical='center', wrap_text=True)


def _status_fill(status: ValidationStatus):
    """Get fill color for validation status."""
    return {
        ValidationStatus.PASS: PASS_FILL,
        ValidationStatus.WARNING: WARN_FILL,
        ValidationStatus.FAIL: FAIL_FILL,
    }.get(status, WARN_FILL)


# ─── Summary Sheet ────────────────────────────────────────────────────────────

def _write_summary_sheet(ws, invoices: list[Invoice]):
    """Write summary dashboard with all invoices."""
    ws.title = "Summary"

    headers = [
        "S.No", "Source File", "PDF Type", "Invoice Number", "Invoice Date",
        "Seller Name", "Seller GSTIN", "Buyer GSTIN",
        "Subtotal (₹)", "Total Tax (₹)", "Grand Total (₹)",
        "Line Items", "Validation Status", "Confidence", "Errors",
    ]

    for col, h in enumerate(headers, 1):
        ws.cell(row=1, column=col, value=h)
    _style_header_row(ws, 1, len(headers))
    ws.freeze_panes = 'A2'

    for i, inv in enumerate(invoices, 1):
        row = i + 1
        ws.cell(row=row, column=1, value=i)
        ws.cell(row=row, column=2, value=inv.source_file)
        ws.cell(row=row, column=3, value=inv.pdf_type.value)
        ws.cell(row=row, column=4, value=str(inv.invoice_number))
        ws.cell(row=row, column=5, value=str(inv.invoice_date))
        ws.cell(row=row, column=6, value=inv.seller.name)
        ws.cell(row=row, column=7, value=inv.seller.gstin)
        ws.cell(row=row, column=8, value=inv.buyer.gstin)
        ws.cell(row=row, column=9, value=inv.subtotal).number_format = CURRENCY_FORMAT
        ws.cell(row=row, column=10, value=inv.total_tax).number_format = CURRENCY_FORMAT
        ws.cell(row=row, column=11, value=inv.grand_total_float).number_format = CURRENCY_FORMAT
        ws.cell(row=row, column=12, value=len(inv.line_items))

        # Validation status with color
        status = inv.validation.status.value if inv.validation else "N/A"
        status_cell = ws.cell(row=row, column=13, value=status)
        if inv.validation:
            status_cell.fill = _status_fill(inv.validation.status)

        ws.cell(row=row, column=14, value=f"{inv.overall_confidence:.0%}")
        ws.cell(row=row, column=15, value="; ".join(inv.processing_errors[:3]))

        # Alternating row colors
        if i % 2 == 0:
            for col in range(1, len(headers) + 1):
                ws.cell(row=row, column=col).fill = ALT_ROW_FILL

        # Borders
        for col in range(1, len(headers) + 1):
            ws.cell(row=row, column=col).border = THIN_BORDER

    _auto_width(ws)


# ─── Individual Invoice Sheet ─────────────────────────────────────────────────

def _write_invoice_sheet(ws, inv: Invoice, sheet_name: str):
    """Write detailed invoice data to a sheet."""
    ws.title = sheet_name[:31]  # Excel sheet name limit

    row = 1

    # ── Header Info ────────────────────────────────────────────────────────
    header_fields = [
        ("Invoice Number", str(inv.invoice_number)),
        ("Invoice Date", str(inv.invoice_date)),
        ("Due Date", inv.due_date),
        ("PO Number", inv.po_number),
        ("Place of Supply", inv.place_of_supply),
        ("IRN", inv.irn),
        ("Ack Number", inv.ack_number),
        ("PDF Type", inv.pdf_type.value),
        ("Extraction Method", inv.extraction_method.value),
    ]

    ws.cell(row=row, column=1, value="INVOICE DETAILS")
    ws.cell(row=row, column=1).font = Font(name='Calibri', bold=True, size=14, color='1F4E79')
    row += 1

    for label, value in header_fields:
        if value:
            ws.cell(row=row, column=1, value=label).font = BOLD_FONT
            ws.cell(row=row, column=2, value=value).font = NORMAL_FONT
            row += 1

    row += 1

    # ── Seller Info ────────────────────────────────────────────────────────
    ws.cell(row=row, column=1, value="SELLER").font = Font(bold=True, size=12, color='1F4E79')
    ws.cell(row=row, column=3, value="BUYER").font = Font(bold=True, size=12, color='1F4E79')
    row += 1

    for label, s_val, b_val in [
        ("Name", inv.seller.name, inv.buyer.name),
        ("GSTIN", inv.seller.gstin, inv.buyer.gstin),
        ("Address", inv.seller.address, inv.buyer.address),
        ("State", inv.seller.state, inv.buyer.state),
        ("State Code", inv.seller.state_code, inv.buyer.state_code),
        ("PAN", inv.seller.pan, inv.buyer.pan),
    ]:
        ws.cell(row=row, column=1, value=label).font = BOLD_FONT
        ws.cell(row=row, column=2, value=s_val)
        ws.cell(row=row, column=3, value=label).font = BOLD_FONT
        ws.cell(row=row, column=4, value=b_val)
        row += 1

    row += 1

    # ── Line Items Table ───────────────────────────────────────────────────
    ws.cell(row=row, column=1, value="LINE ITEMS").font = Font(bold=True, size=12, color='1F4E79')
    row += 1

    item_headers = ["Sr", "Description", "HSN/SAC", "Qty", "Unit", "Rate",
                     "Taxable Amt", "GST %", "CGST", "SGST", "IGST", "Total"]
    for col, h in enumerate(item_headers, 1):
        ws.cell(row=row, column=col, value=h)
    _style_header_row(ws, row, len(item_headers))
    row += 1

    for i, item in enumerate(inv.line_items):
        ws.cell(row=row, column=1, value=item.sr_no or i + 1)
        ws.cell(row=row, column=2, value=item.description)
        ws.cell(row=row, column=3, value=item.hsn_sac)
        ws.cell(row=row, column=4, value=item.quantity).number_format = '#,##0.00'
        ws.cell(row=row, column=5, value=item.unit)
        ws.cell(row=row, column=6, value=item.unit_price).number_format = CURRENCY_FORMAT
        ws.cell(row=row, column=7, value=item.taxable_amount).number_format = CURRENCY_FORMAT
        ws.cell(row=row, column=8, value=f"{item.gst_rate}%")
        ws.cell(row=row, column=9, value=item.cgst_amount).number_format = CURRENCY_FORMAT
        ws.cell(row=row, column=10, value=item.sgst_amount).number_format = CURRENCY_FORMAT
        ws.cell(row=row, column=11, value=item.igst_amount).number_format = CURRENCY_FORMAT
        ws.cell(row=row, column=12, value=item.total_amount).number_format = CURRENCY_FORMAT

        # Borders + alternating colors
        for col in range(1, len(item_headers) + 1):
            cell = ws.cell(row=row, column=col)
            cell.border = THIN_BORDER
            if i % 2 == 1:
                cell.fill = ALT_ROW_FILL
        row += 1

    # Totals row
    row += 1
    ws.cell(row=row, column=1, value="TOTALS").font = BOLD_FONT
    ws.cell(row=row, column=6, value="Subtotal:").font = BOLD_FONT
    ws.cell(row=row, column=7, value=inv.subtotal).number_format = CURRENCY_FORMAT
    row += 1
    ws.cell(row=row, column=6, value="Total Tax:").font = BOLD_FONT
    ws.cell(row=row, column=7, value=inv.total_tax).number_format = CURRENCY_FORMAT
    row += 1
    ws.cell(row=row, column=6, value="Round Off:").font = BOLD_FONT
    ws.cell(row=row, column=7, value=inv.round_off).number_format = CURRENCY_FORMAT
    row += 1
    ws.cell(row=row, column=6, value="Grand Total:").font = Font(bold=True, size=12)
    ws.cell(row=row, column=7, value=inv.grand_total_float)
    ws.cell(row=row, column=7).number_format = CURRENCY_FORMAT
    ws.cell(row=row, column=7).font = Font(bold=True, size=12)
    row += 1

    if inv.amount_in_words:
        ws.cell(row=row, column=1, value=f"Amount in words: {inv.amount_in_words}").font = NORMAL_FONT
        row += 1

    row += 1

    # ── Tax Breakdown ──────────────────────────────────────────────────────
    if inv.tax_details:
        ws.cell(row=row, column=1, value="TAX BREAKDOWN").font = Font(bold=True, size=12, color='1F4E79')
        row += 1
        tax_headers = ["Tax Type", "Rate (%)", "Taxable Amount", "Tax Amount"]
        for col, h in enumerate(tax_headers, 1):
            ws.cell(row=row, column=col, value=h)
        _style_header_row(ws, row, len(tax_headers))
        row += 1

        for td in inv.tax_details:
            ws.cell(row=row, column=1, value=td.tax_type)
            ws.cell(row=row, column=2, value=f"{td.rate}%")
            ws.cell(row=row, column=3, value=td.taxable_amount).number_format = CURRENCY_FORMAT
            ws.cell(row=row, column=4, value=td.tax_amount).number_format = CURRENCY_FORMAT
            for col in range(1, 5):
                ws.cell(row=row, column=col).border = THIN_BORDER
            row += 1

    row += 1

    # ── Bank Details ───────────────────────────────────────────────────────
    if inv.bank.bank_name:
        ws.cell(row=row, column=1, value="BANK DETAILS").font = Font(bold=True, size=12, color='1F4E79')
        row += 1
        for label, val in [
            ("Bank Name", inv.bank.bank_name),
            ("Account No", inv.bank.account_number),
            ("IFSC Code", inv.bank.ifsc_code),
            ("Branch", inv.bank.branch),
            ("SWIFT Code", inv.bank.swift_code),
        ]:
            if val:
                ws.cell(row=row, column=1, value=label).font = BOLD_FONT
                ws.cell(row=row, column=2, value=val)
                row += 1

    # ── Validation Results ─────────────────────────────────────────────────
    if inv.validation and inv.validation.issues:
        row += 1
        ws.cell(row=row, column=1, value="VALIDATION").font = Font(bold=True, size=12, color='1F4E79')
        row += 1
        for issue in inv.validation.issues:
            ws.cell(row=row, column=1, value=issue.severity.value)
            ws.cell(row=row, column=1).fill = _status_fill(issue.severity)
            ws.cell(row=row, column=2, value=issue.field_name)
            ws.cell(row=row, column=3, value=issue.issue)
            row += 1

    _auto_width(ws)


# ─── Extraction Log Sheet ─────────────────────────────────────────────────────

def _write_log_sheet(ws, invoices: list[Invoice]):
    """Write extraction log with per-invoice processing details."""
    ws.title = "Extraction Log"

    headers = [
        "File", "PDF Type", "Pages", "Extraction Method",
        "Table Source", "Confidence", "Line Items Found",
        "Errors", "Warnings",
    ]

    for col, h in enumerate(headers, 1):
        ws.cell(row=1, column=col, value=h)
    _style_header_row(ws, 1, len(headers))
    ws.freeze_panes = 'A2'

    for i, inv in enumerate(invoices, 1):
        row = i + 1
        ws.cell(row=row, column=1, value=inv.source_file)
        ws.cell(row=row, column=2, value=inv.pdf_type.value)
        ws.cell(row=row, column=3, value=inv.page_count)
        ws.cell(row=row, column=4, value=inv.extraction_method.value)
        ws.cell(row=row, column=5, value="")  # Will be filled by pipeline
        ws.cell(row=row, column=6, value=f"{inv.overall_confidence:.0%}")
        ws.cell(row=row, column=7, value=len(inv.line_items))

        errors = inv.processing_errors
        ws.cell(row=row, column=8, value="; ".join(errors) if errors else "None")

        warnings = []
        if inv.validation:
            warnings = [i.issue for i in inv.validation.issues if i.severity == ValidationStatus.WARNING]
        ws.cell(row=row, column=9, value="; ".join(warnings[:3]) if warnings else "None")

        for col in range(1, len(headers) + 1):
            ws.cell(row=row, column=col).border = THIN_BORDER
            if i % 2 == 0:
                ws.cell(row=row, column=col).fill = ALT_ROW_FILL

    _auto_width(ws)


# ─── Journal Entry Format (Accuron-specific) ──────────────────────────────────

JOURNAL_HEADERS = [
    "L id", "Acc Period", "Trans Date", "Account Code", "Description",
    "Curr Code", "Trans Amount", "Dr_Cr", "Jrnal Type", "Jrnal Source",
    "Reference", "Description", "Asset Code", "Asset Indicator",
    "Asset / Item Qty", "Due Date", "Branch Analysis Code",
    "Product  Analysis Code", "ChannelAnalysisCode",
    "Sub-Channel  Analysis Code", "Underwriting Year  Analysis Code",
    "Employee Code  Analysis Code", "TDS Applicability  Analysis Code",
    "Department   Analysis Code", "Sequence Code  Analysis Code",
    "Vendor Code  Analysis Code", "Invoice Date", "From Date", "To Date",
    "Addl Date 4", "Addl Date 5", "Cheque & NEFT Number",
    "Invoice Number", "Additional Remarks", "Àdditional Remarks 2",
    "CREDENCE DESCRIPTION", "HSN/SAC NO", "Taxable on Amount",
    "Reverse Charge (Y/N)", "Reverse charge %", "Item Details (Sr.No)",
    "Goods/Service", "GST Tax Rate",
    "Orginal Invoice no for Dr/Cr Notes", "Advance Challan No ",
]


def _write_journal_entry_sheet(ws, invoices: list[Invoice]):
    """
    Generate journal entries in Accuron's ERP upload format.
    
    Each invoice generates multiple rows:
    1. Expense line item entries (Debit)
    2. GST input credit entries — SGST (Debit)
    3. GST input credit entries — CGST (Debit)
    4. Vendor payable (Credit)
    
    Fields that require internal accounting codes are left blank
    with placeholder comments — these need to be filled by the
    accounting team.
    """
    ws.title = "Journal Entries"

    # Write headers
    for col, h in enumerate(JOURNAL_HEADERS, 1):
        ws.cell(row=1, column=col, value=h)
    _style_header_row(ws, 1, len(JOURNAL_HEADERS))
    ws.freeze_panes = 'A2'

    row = 2

    for inv in invoices:
        inv_num = str(inv.invoice_number) or "UNKNOWN"
        inv_date = str(inv.invoice_date) or ""
        seller_gstin = inv.seller.gstin or ""
        buyer_gstin = inv.buyer.gstin or ""
        seller_name = inv.seller.name or ""

        # Determine accounting period from date
        acc_period = ""
        try:
            from dateutil import parser as dp
            parsed_date = dp.parse(inv_date, dayfirst=True, fuzzy=True)
            month = parsed_date.month
            year = parsed_date.year
            # Indian financial year: Apr=001, Mar=012
            fy_start = year if month >= 4 else year - 1
            period_num = month - 3 if month >= 4 else month + 9
            acc_period = f"{fy_start}/{period_num:03d}"
        except Exception:
            pass

        # Group line items by GST rate for journal entries
        gst_groups: dict[float, list] = {}
        for item in inv.line_items:
            rate = item.gst_rate
            if rate not in gst_groups:
                gst_groups[rate] = []
            gst_groups[rate].append(item)

        # Determine tax type (IGST if inter-state, CGST+SGST if intra-state)
        is_igst = any(td.tax_type == "IGST" for td in inv.tax_details)

        for gst_rate, items in gst_groups.items():
            group_taxable = sum(i.taxable_amount for i in items)
            group_desc = items[0].description if items else ""
            hsn_codes = list(set(i.hsn_sac for i in items if i.hsn_sac))
            hsn_str = hsn_codes[0] if hsn_codes else ""

            if is_igst:
                tax_amount = group_taxable * gst_rate / 100
            else:
                tax_amount = group_taxable * gst_rate / 200  # Half for CGST, half for SGST

            # Row 1: Expense entry (Debit)
            ws.cell(row=row, column=2, value=acc_period)
            ws.cell(row=row, column=3, value=inv_date)
            ws.cell(row=row, column=4, value="[EXPENSE GL CODE]")
            ws.cell(row=row, column=5, value=group_desc[:50])
            ws.cell(row=row, column=6, value="INR")
            ws.cell(row=row, column=7, value=round(group_taxable, 2))
            ws.cell(row=row, column=8, value="D")
            ws.cell(row=row, column=9, value="SEXPS")
            ws.cell(row=row, column=11, value=f"{seller_name[:30]}")
            ws.cell(row=row, column=12, value=group_desc[:50])
            ws.cell(row=row, column=27, value=inv_date)  # Invoice Date
            ws.cell(row=row, column=32, value=seller_gstin)  # Vendor GST
            ws.cell(row=row, column=33, value=inv_num)  # Invoice Number
            ws.cell(row=row, column=44, value=buyer_gstin)  # RS GST number
            row += 1

            if gst_rate > 0:
                if is_igst:
                    # Row 2: IGST input credit (Debit)
                    igst_amount = group_taxable * gst_rate / 100
                    ws.cell(row=row, column=2, value=acc_period)
                    ws.cell(row=row, column=3, value=inv_date)
                    ws.cell(row=row, column=4, value="[IGST INPUT GL]")
                    ws.cell(row=row, column=5, value="GST input cr exps-IGST")
                    ws.cell(row=row, column=6, value="INR")
                    ws.cell(row=row, column=7, value=round(igst_amount, 2))
                    ws.cell(row=row, column=8, value="D")
                    ws.cell(row=row, column=9, value="SEXPS")
                    ws.cell(row=row, column=11, value=f"{seller_name[:30]}")
                    ws.cell(row=row, column=12, value=group_desc[:50])
                    ws.cell(row=row, column=27, value=inv_date)
                    ws.cell(row=row, column=32, value=seller_gstin)
                    ws.cell(row=row, column=33, value=inv_num)
                    ws.cell(row=row, column=37, value=hsn_str)
                    ws.cell(row=row, column=38, value=round(group_taxable, 2))
                    ws.cell(row=row, column=43, value=gst_rate)
                    ws.cell(row=row, column=44, value=buyer_gstin)
                    row += 1
                else:
                    # Row 2: SGST input credit (Debit)
                    ws.cell(row=row, column=2, value=acc_period)
                    ws.cell(row=row, column=3, value=inv_date)
                    ws.cell(row=row, column=4, value="[SGST INPUT GL]")
                    ws.cell(row=row, column=5, value="GST input cr exps-SGST")
                    ws.cell(row=row, column=6, value="INR")
                    ws.cell(row=row, column=7, value=round(tax_amount, 2))
                    ws.cell(row=row, column=8, value="D")
                    ws.cell(row=row, column=9, value="SEXPS")
                    ws.cell(row=row, column=11, value=f"{seller_name[:30]}")
                    ws.cell(row=row, column=12, value=group_desc[:50])
                    ws.cell(row=row, column=27, value=inv_date)
                    ws.cell(row=row, column=32, value=seller_gstin)
                    ws.cell(row=row, column=33, value=inv_num)
                    ws.cell(row=row, column=37, value=hsn_str)
                    ws.cell(row=row, column=38, value=round(group_taxable, 2))
                    ws.cell(row=row, column=43, value=gst_rate / 2)
                    ws.cell(row=row, column=44, value=buyer_gstin)
                    row += 1

                    # Row 3: CGST input credit (Debit)
                    ws.cell(row=row, column=2, value=acc_period)
                    ws.cell(row=row, column=3, value=inv_date)
                    ws.cell(row=row, column=4, value="[CGST INPUT GL]")
                    ws.cell(row=row, column=5, value="GST input cr exps-CGST")
                    ws.cell(row=row, column=6, value="INR")
                    ws.cell(row=row, column=7, value=round(tax_amount, 2))
                    ws.cell(row=row, column=8, value="D")
                    ws.cell(row=row, column=9, value="SEXPS")
                    ws.cell(row=row, column=11, value=f"{seller_name[:30]}")
                    ws.cell(row=row, column=12, value=group_desc[:50])
                    ws.cell(row=row, column=27, value=inv_date)
                    ws.cell(row=row, column=32, value=seller_gstin)
                    ws.cell(row=row, column=33, value=inv_num)
                    ws.cell(row=row, column=37, value=hsn_str)
                    ws.cell(row=row, column=38, value=round(group_taxable, 2))
                    ws.cell(row=row, column=43, value=gst_rate / 2)
                    ws.cell(row=row, column=44, value=buyer_gstin)
                    row += 1

        # Vendor payable entry (Credit) — total invoice amount
        if inv.grand_total_float > 0:
            desc_text = inv.line_items[0].description[:50] if inv.line_items else seller_name[:50]
            ws.cell(row=row, column=2, value=acc_period)
            ws.cell(row=row, column=3, value=inv_date)
            ws.cell(row=row, column=4, value=f"[VENDOR CODE]")
            ws.cell(row=row, column=5, value=seller_name[:50])
            ws.cell(row=row, column=6, value="INR")
            ws.cell(row=row, column=7, value=round(inv.grand_total_float, 2))
            ws.cell(row=row, column=8, value="C")
            ws.cell(row=row, column=9, value="SEXPS")
            ws.cell(row=row, column=11, value=inv.source_file.split('.')[0][:20])
            ws.cell(row=row, column=12, value=desc_text)
            ws.cell(row=row, column=27, value=inv_date)
            ws.cell(row=row, column=32, value=seller_gstin)
            ws.cell(row=row, column=33, value=inv_num)
            row += 1

        # Empty row separator between invoices
        row += 1

    _auto_width(ws)


# ─── Main Generator ──────────────────────────────────────────────────────────

def generate_workbook(invoices: list[Invoice], output_path: str,
                      include_journal: bool = True):
    """
    Generate the complete Excel workbook.
    
    Sheets:
    1. Summary — all invoices overview
    2. Individual invoice tabs (named by invoice number)
    3. Extraction Log — processing details
    4. Journal Entries — Accuron ERP upload format (optional)
    """
    wb = openpyxl.Workbook()

    # Sheet 1: Summary
    ws_summary = wb.active
    _write_summary_sheet(ws_summary, invoices)

    # Sheets 2-N: Individual invoices
    for i, inv in enumerate(invoices, 1):
        inv_num = str(inv.invoice_number).replace("/", "-")[:20] or f"Invoice-{i}"
        ws = wb.create_sheet()
        _write_invoice_sheet(ws, inv, inv_num)

    # Sheet N+1: Extraction Log
    ws_log = wb.create_sheet()
    _write_log_sheet(ws_log, invoices)

    # Sheet N+2: Journal Entries (Accuron format)
    if include_journal:
        ws_journal = wb.create_sheet()
        _write_journal_entry_sheet(ws_journal, invoices)

    # Save
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    wb.save(output_path)
    logger.info(f"Excel workbook saved: {output_path}")
    return output_path
