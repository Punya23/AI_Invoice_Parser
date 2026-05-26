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
    /* Minimalist White Theme */
    .stApp {
        background: #ffffff;
    }
    
    .main-header {
        font-family: 'Inter', sans-serif;
        font-size: 2.5rem;
        font-weight: 800;
        color: #111827; /* Dark almost black */
        letter-spacing: -0.5px;
        margin-bottom: 0.2rem;
    }
    
    .sub-header {
        font-family: 'Inter', sans-serif;
        font-size: 1.1rem;
        color: #6b7280; /* Medium gray */
        margin-bottom: 2.5rem;
        font-weight: 400;
    }
    
    /* Clean Cards */
    .metric-card {
        background: #ffffff;
        padding: 1.5rem;
        border-radius: 12px;
        border: 1px solid #e5e7eb; /* Light gray border */
        box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.05), 0 2px 4px -1px rgba(0, 0, 0, 0.03);
        text-align: center;
        transition: transform 0.2s ease, box-shadow 0.2s ease;
    }
    .metric-card:hover {
        transform: translateY(-2px);
        box-shadow: 0 10px 15px -3px rgba(0, 0, 0, 0.1), 0 4px 6px -2px rgba(0, 0, 0, 0.05);
    }
    
    /* Status Colors */
    .status-pass { color: #111827; font-weight: 600; }
    .status-warn { color: #4b5563; font-weight: 600; }
    .status-fail { color: #dc2626; font-weight: 600; } /* Keeping red for errors */
    
    /* Monochrome Buttons */
    .stDownloadButton > button, .stButton > button {
        background: #111827 !important; /* Black */
        color: #ffffff !important; /* White */
        border-radius: 8px !important;
        border: 1px solid #111827 !important;
        padding: 0.6rem 2rem !important;
        font-weight: 500 !important;
        box-shadow: none !important;
        transition: all 0.2s ease !important;
    }
    .stDownloadButton > button *, .stButton > button * {
        color: #ffffff !important; /* Force white text for all child elements */
    }
    .stDownloadButton > button:hover, .stButton > button:hover {
        background: #374151 !important; /* Dark gray */
        border: 1px solid #374151 !important;
    }
    .stDownloadButton > button:hover *, .stButton > button:hover * {
        color: #ffffff !important;
    }
    
    /* Expanders & Containers */
    .streamlit-expanderHeader {
        background: #f9fafb !important; /* Very light gray */
        border-radius: 8px !important;
        border: 1px solid #e5e7eb !important;
        color: #111827 !important;
    }
    div[data-testid="stExpander"] {
        background: transparent !important;
        border: none !important;
    }
    div[data-testid="stSidebar"] {
        background: #f9fafb !important; /* Very light gray */
        border-right: 1px solid #e5e7eb;
    }
    
    /* Typography Overrides */
    p, span, div {
        color: #374151; /* Dark gray text for high readability */
    }
</style>
""", unsafe_allow_html=True)


# ─── Header ──────────────────────────────────────────────────────────────────

st.markdown('<div class="main-header">Accuron AI — Invoice Parser</div>', unsafe_allow_html=True)
st.markdown(
    '<div class="sub-header">'
    'Automated Invoice PDF to Excel pipeline | Digital & Scanned Support | '
    'Hybrid Extraction'
    '</div>',
    unsafe_allow_html=True,
)

# ─── Sidebar ──────────────────────────────────────────────────────────────────

with st.sidebar:
    st.markdown("### Pipeline Settings")
    st.markdown("---")
    
    include_journal = st.checkbox("Generate ERP Upload Sheets", value=True,
                                   help="Include Accuron ERP Details and Upload Format sheets")
    
    st.markdown("---")
    st.markdown("### Vision AI Configuration")
    api_key_input = st.text_input(
        "Gemini API Key",
        type="password",
        value=os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY") or "",
        help="Enter your Gemini API Key to activate Multimodal Vision AI parsing."
    )
    
    if api_key_input:
        st.success("Vision AI: Active (High-Accuracy)")
    else:
        st.info("Vision AI: Offline Fallback Active")
        
    st.markdown("---")
    st.markdown("### Architecture")
    st.markdown("""
    ```
    PDF → Classify → Extract → Parse → Validate → Excel
    ```
    
    **Vision AI Mode**: Gemini Multimodal  
    **Local Fallback**: pdfplumber + Tesseract OCR  
    **Validation**: GSTIN, Math, Double-entry Balance
    """)
    
    st.markdown("---")
    st.markdown(
        "Built by **Punya Surana**  \n"
        "For Accuron AI Technologies POC"
    )


# ─── File Upload ──────────────────────────────────────────────────────────────

uploaded_files = st.file_uploader(
    "Upload Invoice PDFs",
    type=["pdf"],
    accept_multiple_files=True,
    help="Drop your invoice PDFs here",
)

if uploaded_files:
    st.markdown(f"**{len(uploaded_files)} file(s) uploaded**")
    
    if st.button("Process Invoices", type="primary", width="stretch"):
        
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
                        ocr_result = extract_with_ocr(tmp_path, dpi=300)
                        tables.extend(ocr_result.all_tables)
                        if profile.pdf_type == "scanned":
                            text = ocr_result.full_text
                            extraction_source = ExtractionSource.OCR
                        else:
                            text += "\n\n" + ocr_result.full_text
                    except ImportError as e:
                        if not api_key_input:
                            st.warning(f"OCR not available for {uploaded_file.name}: {e}")
                        else:
                            logger.info(f"Local OCR not available for {uploaded_file.name}: {e}. Continuing with Vision AI.")
                
                # Step 3: Parse
                inv = None
                if api_key_input:
                    try:
                        from src.vision_parser import parse_invoice_with_vision
                        inv = parse_invoice_with_vision(tmp_path, text, api_key_input)
                        if inv:
                            st.info(f"✨ Parsed {uploaded_file.name} using Multimodal Vision AI!")
                    except Exception as e:
                        st.warning(f"Vision AI parsing failed for {uploaded_file.name}: {e}. Falling back to Local Parser.")
                        
                if not inv:
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
        
        progress.progress(1.0, text="Processing complete")
        
        # Store results in session state
        st.session_state['invoices'] = invoices
        st.session_state['processed'] = True


# ─── Results Display ──────────────────────────────────────────────────────────

if st.session_state.get('processed'):
    invoices = st.session_state['invoices']
    
    st.markdown("---")
    st.markdown("## Results")
    
    # Metrics row
    col1, col2, col3, col4 = st.columns(4)
    
    total = len(invoices)
    passed = sum(1 for inv in invoices if inv.validation and inv.validation.status == ValidationStatus.PASS)
    warnings = sum(1 for inv in invoices if inv.validation and inv.validation.status == ValidationStatus.WARNING)
    failed = sum(1 for inv in invoices if inv.validation and inv.validation.status == ValidationStatus.FAIL)
    
    col1.metric("Total Invoices", total)
    col2.metric("Passed", passed)
    col3.metric("Warnings", warnings)
    col4.metric("Failed", failed)
    
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
    
    st.dataframe(summary_data, width="stretch", hide_index=True)
    
    # Individual invoice details (expandable)
    st.markdown("### Invoice Details")
    
    for inv in invoices:
        status_icon = "Pass" if (inv.validation and inv.validation.status == ValidationStatus.PASS) else "Warning" if (inv.validation and inv.validation.status == ValidationStatus.WARNING) else "Fail"
        
        with st.expander(f"[{status_icon}] {inv.source_file} — {str(inv.invoice_number) or 'N/A'}"):
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
                st.dataframe(items_data, width="stretch", hide_index=True)
            
            # Validation issues
            if inv.validation and inv.validation.issues:
                st.write("**Validation:**")
                for issue in inv.validation.issues:
                    icon = "[Fail]" if issue.severity == ValidationStatus.FAIL else "[Warning]"
                    st.write(f"  {icon} {issue.field_name}: {issue.issue}")
            
            # Errors
            if inv.processing_errors:
                st.write("**Processing Notes:**")
                for err in inv.processing_errors:
                    st.write(f"  - {err}")
    
    # ── Generate & Download Excel ──────────────────────────────────────────
    st.markdown("---")
    st.markdown("### Download Results")
    
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
            label="Download Excel Report",
            data=excel_data,
            file_name="accuron_invoice_report.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            width="stretch",
        )
    with col2:
        st.info(
            f"**Workbook contains:**  \n"
            f"- Summary Dashboard  \n"
            f"- {len(invoices)} Invoice Detail Sheets  \n"
            f"- {'Journal: upload format' if include_journal else ''}"
        )

else:
    # Empty state
    st.markdown("---")
    st.markdown(
        "<div style='text-align: center; padding: 3rem; color: #999;'>"
        "<h3>Upload invoice PDFs to get started</h3>"
        "<p>Supports digital and scanned PDFs • Extracts all fields automatically</p>"
        "</div>",
        unsafe_allow_html=True,
    )
