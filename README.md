# Accuron AI — Invoice Parser POC

> **Automated Invoice PDF to Structured Excel pipeline**  
> Handles both **digital** and **scanned** PDFs with deterministic regex-based extraction

---

## What It Does

Processes invoice PDFs and extracts structured data into a multi-sheet Excel workbook:

### Workflow Summary
1. **Input**: Digital or scanned invoice PDF files.
2. **Output**: Multi-sheet Excel workbook (.xlsx) containing:
   * **Summary Dashboard**: Overview metrics for all processed files.
   * **Individual Invoice Details**: Tabular detail reviews for each document.
   * **details to be captured (ERP Guide)**: Accuron column reference rules.
   * **upload format (ERP Journal Entry)**: Balanced double-entry 56-column journal ledger.

### Fields Extracted
- Invoice Number, Date, Due Date
- Seller & Buyer details (Name, GSTIN, Address, State Code)
- Line items (Description, HSN/SAC, Qty, Rate, Amount, GST)
- Tax breakdown (CGST, SGST, IGST with rates)
- Grand Total, Round-off, Amount in Words
- Bank details, IRN, e-Invoice Acknowledgement
- Place of Supply, Reverse Charge flag

---

## Pipeline Architecture

The invoice processing pipeline runs sequentially through the following modules:

1. **PDF Input**: Receives incoming digital or scanned document uploads.
2. **Classifier**: Checks for native text layers to decide the parsing route.
3. **Extractor**: Extracts raw text and table contents using pdfplumber (for digital text) or Tesseract OCR (for scanned pages).
4. **Parser**: Translates extracted text to structures via regex heuristics or Google Gemini Vision AI.
5. **Validator**: Runs GSTIN format checks and mathematical balancing tests.
6. **Excel Generator**: Writes a professionally styled, multi-sheet workbook using openpyxl.

### Design Decisions

| Decision | Why |
|---|---|
| **Hybrid Approach (Regex + Vision AI)** | Fast regex parsing for standard layouts, fallback to Gemini 1.5 Flash Vision API for complex/unstructured tables |
| **Deterministic Base** | Core extraction remains deterministic and auditable; AI only handles the heavily unstructured tables |
| **Tesseract over PaddleOCR** | 30MB vs 1.5GB install, 0.5s vs 3-5s/page, production-friendly |
| **pdfplumber over PyPDF** | Superior table extraction, word-level bounding boxes |
| **Field-level confidence** | Critical fields (invoice#, date, total) carry individual scores |
| **Error isolation** | One failed PDF never blocks other invoices |

---

## Setup and Installation

Follow these steps to set up the invoice parser locally or deploy it to a hosting server.

### 1. Prerequisites (System Libraries)

Since the pipeline uses Tesseract for scanned PDF OCR, you need to install the Tesseract system binary.

* **macOS** (using Homebrew):
  ```bash
  brew install tesseract
  ```
* **Ubuntu/Debian**:
  ```bash
  sudo apt update
  sudo apt install tesseract-ocr libtesseract-dev
  ```
* **Windows**:
  Download and install the Tesseract installer from the [UB Mannheim project](https://github.com/UB-Mannheim/tesseract/wiki), and add the installation folder to your Windows System PATH.

### 2. Local Installation

Clone the repository and install the Python dependencies:

```bash
git clone https://github.com/Punya23/AI_Invoice_Parser.git
cd AI_Invoice_Parser

# Set up a virtual environment
python -m venv venv
source venv/bin/activate  # On Windows, use: venv\Scripts\activate

# Install requirements
pip install -r requirements.txt
```

### 3. Running the Streamlit Web Interface

To launch the web dashboard locally:

```bash
streamlit run app.py
```

Once running, open your browser to `http://localhost:8501`. 

* **Configuring Vision AI (Optional)**: If you wish to use Google Gemini Multimodal parsing, enter your Gemini API Key in the sidebar input box of the web app, or set the environment variable:
  ```bash
  export GEMINI_API_KEY="your-api-key"
  ```
  If no key is configured, the app will automatically fall back to the offline local parser.

### 4. Running the CLI Orchestrator

To parse documents from the command line:

```bash
# Parse a single file
python main.py --input path/to/invoice.pdf --output output/result.xlsx

# Parse a directory of files
python main.py --input path/to/invoices_folder/ --output output/result.xlsx

# Parse files while skipping the ERP journal sheets
python main.py --input path/to/invoice.pdf --output output/result.xlsx --no-journal
```

### 5. Deployment to Streamlit Community Cloud

The codebase is fully structured to deploy directly to Streamlit Community Cloud:

1. Push your repository to your GitHub account.
2. Visit [share.streamlit.io](https://share.streamlit.io/) and log in with GitHub.
3. Click **New app** and choose the `AI_Invoice_Parser` repository.
4. Set the **Main file path** to `app.py` and choose **Python 3.12** or the default version.
5. In **Advanced Settings**, add your API key under Secrets:
   ```toml
   GEMINI_API_KEY = "your-api-key-here"
   ```
6. Click **Deploy**. The server will automatically read `packages.txt` to install the Tesseract system binaries and `requirements.txt` to install Python packages.

---

## Project Structure

```
accuron-invoice-parser/
├── main.py                    # CLI entry point
├── app.py                     # Streamlit web interface
├── requirements.txt           # Python dependencies
├── README.md
├── src/
│   ├── __init__.py
│   ├── models.py              # Data models (Invoice, LineItem, etc.)
│   ├── pdf_detector.py        # PDF type classification (digital/scanned)
│   ├── text_extractor.py      # Digital PDF extraction (pdfplumber + clustering)
│   ├── ocr_extractor.py       # Scanned PDF extraction (Tesseract OCR)
│   ├── vision_parser.py       # Multimodal Vision AI extraction (Gemini 1.5 Flash)
│   ├── invoice_parser.py      # Regex-based field extraction engine
│   ├── validator.py           # GSTIN, math, date, completeness validation
│   └── excel_generator.py     # Multi-sheet Excel workbook generator
├── Document/                  # Sample invoice PDFs (test data)
└── output/                    # Generated Excel reports
```

---

## Technical Details

### PDF Classification
Every PDF is classified before processing:
- **Digital**: Has embedded text layer → pdfplumber (fast, accurate)
- **Scanned**: Image-only → Tesseract OCR with image preprocessing
- **Hybrid**: Mix → digital pages via pdfplumber, scanned via OCR

### Two-Tier Table Extraction
1. **Tier 1**: `pdfplumber.extract_tables()` — works on bordered tables (CIEL HR, Vault Infosec)
2. **Tier 2**: Positional word clustering — fallback for borderless tables (Green Clean, Tally-style invoices)
   - Groups words by Y-coordinate proximity into rows
   - Identifies column boundaries from X-position clustering
   - Reconstructs a 2D table grid

### Regex Engine
- 8-10 pattern aliases per field (handles diverse invoice formats)
- `dateutil.parser` as fallback for date parsing
- Dynamic table column mapping based on header keyword detection
- Tally-format parser for split-line invoice numbers

### Validation Layer
1. **GSTIN validation**: 15-character format check
2. **Mathematical cross-checks**: `qty × rate ≈ amount`, `Σ items ≈ subtotal`, `subtotal + tax ≈ total`
3. **Date sanity**: Parseable, not future-dated, not >5 years old
4. **Completeness**: Required fields present (invoice#, date, total, seller)

### OCR & AI Engine (Modular)
Currently uses **Tesseract** for basic scanned text, and **Gemini 1.5 Flash Vision API** for handling complex table structures that the heuristics miss. Architecture supports drop-in replacement with:
- Google Document AI (cloud-based, highest accuracy)
- AWS Textract (production alternative)

---

## Test Results

Tested against 12 Accuron invoices (5 digital + 7 scanned):

| Invoice | Type | Invoice # | Date | Total | Status |
|---|---|---|---|---|---|
| AWS IT | Digital | AIN2526001124876 | 02/07/2025 | ₹27,12,845.83 | Pass |
| CIEL HR | Digital | IHR030932627 | 28/04/2026 | ₹12,90,543.66 | Pass |
| INUBE IT | Digital | 25-26/463 | 11/02/2026 | ₹21,24,000.00 | Pass |
| Green Clean | Digital | GC/26-27/251 | 17/04/2026 | ₹1,83,900.00 | Pass |
| Vault Infosec | Digital | VIIPL-2627-002 | 02/04/2026 | ₹1,77,000.00 | Pass |
| Tata Power | Scanned (OCR) | Extracted | Extracted | Extracted | Warning |
| OEC Records | Scanned (OCR) | Extracted | Extracted | ₹1,44,356.71 | Warning |
| Professional Couriers | Scanned (OCR) | MAA30080062 | Extracted | ₹974.32 | Pass |
| Casa 2 Stays | Scanned (OCR) | BR/2526/01744 | Extracted | Extracted | Warning |
| Saanvi Trading | Scanned (OCR) | ST/25-26/001 | Extracted | Extracted | Pass |

*Warning = Partially extracted (OCR accuracy varies with scan quality)*

---

## Dependencies

| Package | Version | Purpose | Size |
|---|---|---|---|
| pdfplumber | ≥0.11.0 | Digital PDF text & table extraction | 2MB |
| PyMuPDF | ≥1.24.0 | PDF rendering for OCR (no poppler) | 15MB |
| openpyxl | ≥3.1.0 | Excel workbook generation | 5MB |
| python-dateutil | ≥2.8.0 | Date parsing | 300KB |
| pytesseract | ≥0.3.10 | Tesseract OCR Python wrapper | 50KB |
| Pillow | ≥10.0.0 | Image preprocessing for OCR | 10MB |
| streamlit | ≥1.40.0 | Web interface | 30MB |

**Total install: ~60MB** (vs ~1.5GB with PaddleOCR)

System requirement: `tesseract` binary (`brew install tesseract`)

---

## Production Scaling Notes

This is a POC. For production deployment:

1. **OCR upgrade**: Swap Tesseract for PaddleOCR or Google Document AI for higher accuracy
2. **Async processing**: Queue-based architecture for batch invoice processing  
3. **Template learning**: Auto-detect invoice templates by vendor for improved regex targeting
4. **API layer**: FastAPI wrapper for REST endpoint integration
5. **Database**: PostgreSQL for invoice storage and deduplication
6. **Monitoring**: Field-level confidence dashboards for extraction quality tracking

---

## Author

**Punya Surana**  
Built for Accuron AI Technologies Pvt Ltd — Internship Assignment

---

*Built using Python and custom Hybrid Extraction Pipelines*
