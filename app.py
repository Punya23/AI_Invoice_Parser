"""
app.py — Streamlit Web Interface for Accuron AI Invoice Parser

Professional demo interface:
- Drag & drop PDF upload (single or multiple)
- PDF type detection display (digital/scanned)
- Preview extracted data in expandable tables
- Download Excel workbook
- Validation results with color-coded status
"""

import streamlit as st
import tempfile
import os
import time
from pathlib import Path

from src.pdf_detector import classify_pdf
from src.text_extractor import extract_document
from src.ocr_extractor import extract_with_ocr
from src.invoice_parser import parse_invoice
from src.validator import InvoiceValidator
from src.excel_generator import generate_workbook
from src.models import Invoice, PDFType, ExtractionSource, ValidationStatus


# ─── Page Config ──────────────────────────────────────────────────────────────

st.set_page_config(
    page_title="Accuron AI — Invoice Parser",
    page_icon="📄",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ─── Custom CSS ───────────────────────────────────────────────────────────────

st.markdown("""
<style>
    .main-header {
        font-size: 2.2rem;
        font-weight: 700;
        color: #1F4E79;
        margin-bottom: 0.5rem;
    }
    .sub-header {
        font-size: 1rem;
        color: #666;
        margin-bottom: 2rem;
    }
    .metric-card {
        background: linear-gradient(135deg, #f5f7fa 0%, #c3cfe2 100%);
        padding: 1.2rem;
        border-radius: 10px;
        text-align: center;
    }
    .status-pass { color: #27ae60; font-weight: bold; }
    .status-warn { color: #f39c12; font-weight: bold; }
    .status-fail { color: #e74c3c; font-weight: bold; }
    .stDownloadButton > button {
        background-color: #1F4E79;
        color: white;
        border-radius: 8px;
        padding: 0.5rem 2rem;
    }
</style>
""", unsafe_allow_html=True)


# ─── Header ──────────────────────────────────────────────────────────────────

st.markdown('<div class="main-header">📄 Accuron AI — Invoice Parser</div>', unsafe_allow_html=True)
st.markdown(
    '<div class="sub-header">'
    'Automated Invoice PDF → Excel pipeline | Digital & Scanned PDF support | '
    'Regex-based deterministic extraction'
    '</div>',
    unsafe_allow_html=True,
)

# ─── Sidebar ──────────────────────────────────────────────────────────────────

with st.sidebar:
    st.markdown("### ⚙️ Pipeline Settings")
    st.markdown("---")
    
    ocr_dpi = st.slider("OCR Resolution (DPI)", 150, 400, 300, 50,
                         help="Higher DPI = better accuracy but slower")
    
    include_journal = st.checkbox("Generate Journal Entries", value=True,
                                   help="Include Accuron ERP upload format sheet")
    
    st.markdown("---")
    st.markdown("### 📊 Architecture")
    st.markdown("""
    ```
    PDF → Classify → Extract → Parse → Validate → Excel
    ```
    
    **Digital PDFs**: pdfplumber  
    **Scanned PDFs**: Tesseract OCR  
    **Parsing**: Regex + Table clustering  
    **Validation**: GSTIN, math, dates
    """)
    
    st.markdown("---")
    st.markdown(
        "Built by **Punya Surana**  \n"
        "For Accuron AI Technologies POC"
    )


# ─── File Upload ──────────────────────────────────────────────────────────────

uploaded_files = st.file_uploader(
    "📁 Upload Invoice PDFs",
    type=["pdf"],
    accept_multiple_files=True,
    help="Drop your invoice PDFs here — supports both digital and scanned documents",
)

if uploaded_files:
    st.markdown(f"**{len(uploaded_files)} file(s) uploaded**")
    
    if st.button("🚀 Process Invoices", type="primary", use_container_width=True):
        
        invoices: list[Invoice] = []
        progress = st.progress(0, text="Starting pipeline...")
        
        for i, uploaded_file in enumerate(uploaded_files):
            progress.progress(
                (i) / len(uploaded_files),
                text=f"Processing {uploaded_file.name}..."
            )
            
            # Save uploaded file to temp location
            with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
                tmp.write(uploaded_file.read())
                tmp_path = tmp.name
            
            try:
                # Step 1: Classify
                profile = classify_pdf(tmp_path)
                
                # Step 2: Extract
                text = ""
                tables = []
                extraction_source = ExtractionSource.PDFPLUMBER_TEXT
                
                if profile.pdf_type in ("digital", "hybrid"):
                    doc = extract_document(tmp_path)
                    text = doc.full_text
                    tables = doc.all_tables
                
                if profile.pdf_type in ("scanned", "hybrid"):
                    try:
                        ocr_result = extract_with_ocr(tmp_path, dpi=ocr_dpi)
                        if profile.pdf_type == "scanned":
                            text = ocr_result.full_text
                            extraction_source = ExtractionSource.OCR
                        else:
                            text += "\n\n" + ocr_result.full_text
                    except ImportError as e:
                        st.warning(f"OCR not available for {uploaded_file.name}: {e}")
                
                # Step 3: Parse
                inv = parse_invoice(
                    text=text, tables=tables,
                    source_file=uploaded_file.name,
                    pdf_type=PDFType(profile.pdf_type),
                    extraction_source=extraction_source,
                )
                inv.page_count = profile.page_count
                
                # Step 4: Validate
                validator = InvoiceValidator()
                inv.validation = validator.validate(inv)
                
                invoices.append(inv)
                
            except Exception as e:
                inv = Invoice(source_file=uploaded_file.name)
                inv.processing_errors.append(f"Error: {str(e)}")
                invoices.append(inv)
            
            finally:
                os.unlink(tmp_path)
        
        progress.progress(1.0, text="✅ Processing complete!")
        
        # Store results in session state
        st.session_state['invoices'] = invoices
        st.session_state['processed'] = True


# ─── Results Display ──────────────────────────────────────────────────────────

if st.session_state.get('processed'):
    invoices = st.session_state['invoices']
    
    st.markdown("---")
    st.markdown("## 📊 Results")
    
    # Metrics row
    col1, col2, col3, col4 = st.columns(4)
    
    total = len(invoices)
    passed = sum(1 for inv in invoices if inv.validation and inv.validation.status == ValidationStatus.PASS)
    warnings = sum(1 for inv in invoices if inv.validation and inv.validation.status == ValidationStatus.WARNING)
    failed = sum(1 for inv in invoices if inv.validation and inv.validation.status == ValidationStatus.FAIL)
    
    col1.metric("Total Invoices", total)
    col2.metric("✅ Passed", passed)
    col3.metric("⚠️ Warnings", warnings)
    col4.metric("❌ Failed", failed)
    
    # Summary table
    st.markdown("### 📋 Invoice Summary")
    
    summary_data = []
    for inv in invoices:
        status = inv.validation.status.value if inv.validation else "N/A"
        status_icon = {"PASS": "✅", "WARNING": "⚠️", "FAIL": "❌"}.get(status, "❓")
        
        summary_data.append({
            "File": inv.source_file,
            "Type": inv.pdf_type.value.upper(),
            "Invoice #": str(inv.invoice_number) if str(inv.invoice_number) != "None" else "—",
            "Date": str(inv.invoice_date) if str(inv.invoice_date) != "None" else "—",
            "Seller": inv.seller.name[:30] if inv.seller.name else "—",
            "Grand Total": f"₹{inv.grand_total_float:,.2f}" if inv.grand_total_float else "—",
            "Items": len(inv.line_items),
            "Status": f"{status_icon} {status}",
        })
    
    st.dataframe(summary_data, use_container_width=True, hide_index=True)
    
    # Individual invoice details (expandable)
    st.markdown("### 📄 Invoice Details")
    
    for inv in invoices:
        status_icon = "✅" if (inv.validation and inv.validation.status == ValidationStatus.PASS) else "⚠️" if (inv.validation and inv.validation.status == ValidationStatus.WARNING) else "❌"
        
        with st.expander(f"{status_icon} {inv.source_file} — {str(inv.invoice_number) or 'N/A'}"):
            # Header info
            c1, c2, c3 = st.columns(3)
            c1.write(f"**Invoice #:** {inv.invoice_number}")
            c2.write(f"**Date:** {inv.invoice_date}")
            c3.write(f"**Total:** ₹{inv.grand_total_float:,.2f}")
            
            c1, c2 = st.columns(2)
            with c1:
                st.write("**Seller:**")
                st.write(f"  {inv.seller.name}")
                st.write(f"  GSTIN: {inv.seller.gstin}")
            with c2:
                st.write("**Buyer:**")
                st.write(f"  {inv.buyer.name}")
                st.write(f"  GSTIN: {inv.buyer.gstin}")
            
            # Line items table
            if inv.line_items:
                st.write("**Line Items:**")
                items_data = []
                for item in inv.line_items:
                    items_data.append({
                        "Sr": item.sr_no,
                        "Description": item.description[:50],
                        "HSN/SAC": item.hsn_sac,
                        "Qty": item.quantity,
                        "Rate": f"₹{item.unit_price:,.2f}",
                        "Amount": f"₹{item.taxable_amount:,.2f}",
                        "GST %": f"{item.gst_rate}%",
                    })
                st.dataframe(items_data, use_container_width=True, hide_index=True)
            
            # Validation issues
            if inv.validation and inv.validation.issues:
                st.write("**Validation:**")
                for issue in inv.validation.issues:
                    icon = "❌" if issue.severity == ValidationStatus.FAIL else "⚠️"
                    st.write(f"  {icon} {issue.field_name}: {issue.issue}")
            
            # Errors
            if inv.processing_errors:
                st.write("**Processing Notes:**")
                for err in inv.processing_errors:
                    st.write(f"  ℹ️ {err}")
    
    # ── Generate & Download Excel ──────────────────────────────────────────
    st.markdown("---")
    st.markdown("### 📥 Download Results")
    
    # Generate Excel in memory
    with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as tmp:
        tmp_output = tmp.name
    
    generate_workbook(invoices, tmp_output, include_journal=st.session_state.get('include_journal', True))
    
    with open(tmp_output, "rb") as f:
        excel_data = f.read()
    os.unlink(tmp_output)
    
    col1, col2 = st.columns(2)
    with col1:
        st.download_button(
            label="📥 Download Excel Report",
            data=excel_data,
            file_name="accuron_invoice_report.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            use_container_width=True,
        )
    with col2:
        st.info(
            f"**Workbook contains:**  \n"
            f"📊 Summary Dashboard  \n"
            f"📄 {len(invoices)} Invoice Detail Sheets  \n"
            f"📋 Extraction Log  \n"
            f"{'📒 Journal Entry Format' if include_journal else ''}"
        )

else:
    # Empty state
    st.markdown("---")
    st.markdown(
        "<div style='text-align: center; padding: 3rem; color: #999;'>"
        "<h3>👆 Upload invoice PDFs to get started</h3>"
        "<p>Supports digital and scanned PDFs • Extracts all fields automatically</p>"
        "</div>",
        unsafe_allow_html=True,
    )
