import json
import pytest
from pathlib import Path
from main import process_single_invoice

# Determine tests directory relative to this file
TESTS_DIR = Path(__file__).parent
GROUND_TRUTH_DIR = TESTS_DIR / "ground_truth"

def load_ground_truth(filename: str) -> dict:
    """Load expected JSON output for a given test PDF."""
    json_path = GROUND_TRUTH_DIR / f"{Path(filename).stem}.json"
    if not json_path.exists():
        return {}
    with open(json_path, 'r') as f:
        return json.load(f)

@pytest.fixture
def test_pdfs():
    """Find all test PDFs in the Document directory."""
    doc_dir = TESTS_DIR.parent / "Document"
    if not doc_dir.exists():
        doc_dir = Path("/Users/punyasurana/Documents/Test_Document")
    if not doc_dir.exists():
        pytest.skip(f"Document directory not found")
    pdfs = list(doc_dir.glob("*.pdf"))
    if not pdfs:
        pytest.skip("No PDFs found in Document directory")
    return pdfs

def test_pipeline_accuracy(test_pdfs):
    """
    Test extraction accuracy against ground truth.
    If ground truth doesn't exist, this acts as a smoke test to ensure
    the pipeline doesn't crash on the document.
    """
    results = []
    
    for pdf_path in test_pdfs:
        print(f"Testing extraction on {pdf_path.name}...")
        
        # Act
        try:
            invoice = process_single_invoice(str(pdf_path))
        except Exception as e:
            pytest.fail(f"Pipeline crashed on {pdf_path.name}: {str(e)}")
            
        # Load expected
        expected = load_ground_truth(pdf_path.name)
        if not expected:
            print(f"[SKIP] No ground truth JSON for {pdf_path.name}")
            continue
            
        # Assert Core Fields
        assert invoice.invoice_number.value == expected.get("invoice_number"), f"Invoice # mismatch on {pdf_path.name}"
        assert invoice.grand_total.value == str(expected.get("grand_total")), f"Grand total mismatch on {pdf_path.name}"
        
        # Assert Line Items
        expected_items = expected.get("line_items", [])
        assert len(invoice.line_items) == len(expected_items), \
            f"Line item count mismatch on {pdf_path.name}: Expected {len(expected_items)}, got {len(invoice.line_items)}"
            
        # If we got here, it's accurate
        results.append(True)
        
    print(f"Accuracy test complete. Verified {len(results)} documents against ground truth.")
