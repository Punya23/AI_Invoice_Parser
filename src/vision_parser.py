"""
vision_parser.py — Multimodal Vision AI Extraction using Google Gemini API

Processes digital or scanned PDFs:
1. Renders PDF pages to base64 JPEGs in-memory via PyMuPDF.
2. Formulates a REST API call to Gemini (1.5 Flash or Pro) with a structured JSON schema.
3. Enforces response schema output, guaranteeing clean and deterministic JSON.
4. Maps the structured response directly to internal Invoice and LineItem dataclasses.
"""

import os
import io
import json
import base64
import logging
import requests
import fitz  # PyMuPDF
from typing import Optional

from src.models import Invoice, LineItem, PartyInfo, BankDetails, TaxDetail, ExtractedField, PDFType, ExtractionSource

logger = logging.getLogger(__name__)

# Structured JSON Response Schema for Gemini
GEMINI_RESPONSE_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "invoice_number": {"type": "STRING", "description": "The invoice number or invoice identifier"},
        "invoice_date": {"type": "STRING", "description": "Invoice date in DD/MM/YYYY format"},
        "due_date": {"type": "STRING", "description": "Due date in DD/MM/YYYY format if present"},
        "place_of_supply": {"type": "STRING", "description": "Place of supply or state name"},
        "reverse_charge": {"type": "STRING", "description": "'Yes' or 'No' depending on whether reverse charge is applicable"},
        "seller": {
            "type": "OBJECT",
            "properties": {
                "name": {"type": "STRING", "description": "Name of the seller / supplier company"},
                "gstin": {"type": "STRING", "description": "15-digit GSTIN of the seller"},
                "address": {"type": "STRING", "description": "Full address of the seller"},
                "pan": {"type": "STRING", "description": "PAN number of the seller"}
            },
            "required": ["name"]
        },
        "buyer": {
            "type": "OBJECT",
            "properties": {
                "name": {"type": "STRING", "description": "Name of the buyer / billing company"},
                "gstin": {"type": "STRING", "description": "15-digit GSTIN of the buyer"},
                "address": {"type": "STRING", "description": "Full address of the buyer"},
                "pan": {"type": "STRING", "description": "PAN number of the buyer"}
            },
            "required": ["name"]
        },
        "subtotal": {"type": "NUMBER", "description": "Total taxable value before GST taxes"},
        "grand_total": {"type": "NUMBER", "description": "Final invoice total amount including taxes"},
        "round_off": {"type": "NUMBER", "description": "Rounding off adjustments if present"},
        "amount_in_words": {"type": "STRING", "description": "Grand total amount in words"},
        "line_items": {
            "type": "ARRAY",
            "description": "List of line items or charges extracted from the tables",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "sr_no": {"type": "INTEGER", "description": "Serial number"},
                    "description": {"type": "STRING", "description": "Description of service or goods"},
                    "hsn_sac": {"type": "STRING", "description": "HSN or SAC code"},
                    "quantity": {"type": "NUMBER", "description": "Quantity of the item, default to 1.0 if not specified"},
                    "unit": {"type": "STRING", "description": "Unit of measure, e.g. pc, nos, hrs, mon, services"},
                    "unit_price": {"type": "NUMBER", "description": "Unit rate or price per item"},
                    "taxable_amount": {"type": "NUMBER", "description": "Taxable base value for this line item"},
                    "gst_rate": {"type": "NUMBER", "description": "Total GST rate percentage for this line, e.g. 18.0 or 5.0 or 0.0"}
                },
                "required": ["description", "taxable_amount"]
            }
        },
        "bank_details": {
            "type": "OBJECT",
            "properties": {
                "bank_name": {"type": "STRING", "description": "Bank name"},
                "account_number": {"type": "STRING", "description": "Bank account number"},
                "ifsc_code": {"type": "STRING", "description": "IFSC code"},
                "swift_code": {"type": "STRING", "description": "SWIFT code"}
            }
        }
    },
    "required": ["invoice_number", "invoice_date", "grand_total", "seller", "buyer", "line_items"]
}


def _pdf_to_images_b64(pdf_path: str, dpi: int = 200) -> list[str]:
    """Render PDF pages to JPEG base64 strings in-memory."""
    images = []
    try:
        doc = fitz.open(pdf_path)
        for page_num in range(len(doc)):
            page = doc[page_num]
            pix = page.get_pixmap(dpi=dpi)
            img_bytes = pix.tobytes("jpg")
            b64_str = base64.b64encode(img_bytes).decode("utf-8")
            images.append(b64_str)
        doc.close()
    except Exception as e:
        logger.error(f"Failed to render PDF to images: {e}")
    return images


def parse_invoice_with_vision(pdf_path: str, extracted_text: str, api_key: str, pdf_type: PDFType = PDFType.DIGITAL) -> Optional[Invoice]:
    """
    Parse an invoice using Gemini Multimodal Vision API.
    
    1. Render PDF pages to base64 JPEGs.
    2. Package images and extracted text into a single API call.
    3. Enforce structured JSON schema matching our internal Invoice dataclass.
    """
    logger.info(f"Initiating Multimodal Vision AI extraction for: {os.path.basename(pdf_path)}")
    
    # Render pages
    images_b64 = _pdf_to_images_b64(pdf_path, dpi=200)
    if not images_b64:
        logger.error("Could not render any pages from PDF for Vision AI")
        return None

    # Setup REST Endpoint (using gemini-flash-latest for speed and structural accuracy)
    model = "gemini-flash-latest"
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
    
    # System instruction for financial data extraction
    system_instruction = (
        "You are an expert accounting and audit assistant extracting structured data for an ERP system. "
        "CRITICAL RULES FOR LINE ITEMS:\n"
        "1. Extract ONLY the top-level billing items (e.g., 'Courier Charges', 'Freight', 'Service Fee') that form the taxable base.\n"
        "2. ABSOLUTELY IGNORE 'Statement of Transaction', annexures, waybill logs, or detailed shipment breakdowns (e.g. lists of tracking numbers/dates).\n"
        "3. NEVER hallucinate or invent 'quantity' or 'unit_price' to force math to work. If the invoice only provides a lump sum amount, set quantity=1 and unit_price=amount.\n"
        "4. Extract exact HSN/SAC codes if present.\n"
        "5. Format dates strictly as DD/MM/YYYY."
    )

    # Build parts: text prompt + all images
    prompt_text = (
        "Analyze the provided invoice images and text. Extract every field matching the schema. "
        "Pay special attention to the main billing summary. IGNORE detailed transaction logs on subsequent pages. "
        "Ensure the sum of the extracted line items equals the total taxable amount.\n\n"
        f"Supplementary Extracted Text:\n{extracted_text}"
    )
    
    parts = [{"text": prompt_text}]
    for img_b64 in images_b64:
        parts.append({
            "inlineData": {
                "mimeType": "image/jpeg",
                "data": img_b64
            }
        })
        
    payload = {
        "contents": [{"parts": parts}],
        "systemInstruction": {"parts": [{"text": system_instruction}]},
        "generationConfig": {
            "responseMimeType": "application/json",
            "responseSchema": GEMINI_RESPONSE_SCHEMA
        }
    }
    
    headers = {"Content-Type": "application/json"}
    
    try:
        response = requests.post(url, json=payload, headers=headers, timeout=60)
        response.raise_for_status()
        
        response_data = response.json()
        raw_output = response_data["candidates"][0]["content"]["parts"][0]["text"]
        logger.debug(f"Gemini raw output: {raw_output}")
        
        extracted_data = json.loads(raw_output)
        
        # Map JSON dictionary to our models
        return _map_json_to_invoice(extracted_data, os.path.basename(pdf_path), pdf_type)
        
    except Exception as e:
        logger.error(f"Vision AI extraction failed: {e}")
        if 'response' in locals() and response is not None:
            logger.error(f"Response status: {response.status_code} | Body: {response.text[:500]}")
        return None


def _map_json_to_invoice(data: dict, filename: str, pdf_type: PDFType = PDFType.DIGITAL) -> Invoice:
    """Map the structured JSON parsed from Gemini into our internal Invoice dataclass."""
    
    # 1. Parties
    s_data = data.get("seller", {})
    seller = PartyInfo(
        name=s_data.get("name", "").strip(),
        gstin=s_data.get("gstin", "").strip(),
        address=s_data.get("address", "").strip(),
        pan=s_data.get("pan", "").strip()
    )
    
    b_data = data.get("buyer", {})
    buyer = PartyInfo(
        name=b_data.get("name", "").strip(),
        gstin=b_data.get("gstin", "").strip(),
        address=b_data.get("address", "").strip(),
        pan=b_data.get("pan", "").strip()
    )
    
    # 2. Critical Fields
    inv_num_val = data.get("invoice_number", "").strip()
    inv_date_val = data.get("invoice_date", "").strip()
    g_total_val = float(data.get("grand_total") or 0.0)
    
    invoice = Invoice(
        source_file=filename,
        pdf_type=pdf_type,
        extraction_method=ExtractionSource.OCR,
        seller=seller,
        buyer=buyer,
        place_of_supply=data.get("place_of_supply", "").strip(),
        reverse_charge=data.get("reverse_charge", "No").strip(),
        due_date=data.get("due_date", "").strip(),
        subtotal=float(data.get("subtotal") or 0.0),
        round_off=float(data.get("round_off") or 0.0),
        amount_in_words=data.get("amount_in_words", "").strip(),
        invoice_number=ExtractedField.high(inv_num_val, ExtractionSource.OCR),
        invoice_date=ExtractedField.high(inv_date_val, ExtractionSource.OCR),
        grand_total=ExtractedField.high(f"{g_total_val:.2f}", ExtractionSource.OCR)
    )
    
    # 3. Line Items
    is_igst = False
    if seller.gstin and buyer.gstin:
        is_igst = seller.gstin[:2] != buyer.gstin[:2]
        
    raw_items = data.get("line_items", [])
    for idx, ri in enumerate(raw_items, start=1):
        hsn = ri.get("hsn_sac", "").strip()
        desc = ri.get("description", "").strip()
        taxable = float(ri.get("taxable_amount") or 0.0)
        rate = float(ri.get("gst_rate") or 0.0)
        qty = float(ri.get("quantity") or 1.0)
        u_price = float(ri.get("unit_price") or taxable / qty if qty > 0 else taxable)
        
        # Calculate individual taxes per item
        cgst, sgst, igst = 0.0, 0.0, 0.0
        if rate > 0:
            if is_igst:
                igst = round(taxable * rate / 100, 2)
            else:
                cgst = round(taxable * rate / 200, 2)
                sgst = cgst
                
        tot_amt = round(taxable + igst + cgst + sgst, 2)
        
        item = LineItem(
            sr_no=ri.get("sr_no") or idx,
            description=desc,
            hsn_sac=hsn,
            quantity=qty,
            unit=ri.get("unit", "pc").strip(),
            unit_price=round(u_price, 2),
            taxable_amount=round(taxable, 2),
            gst_rate=rate,
            cgst_amount=cgst,
            sgst_amount=sgst,
            igst_amount=igst,
            total_amount=tot_amt
        )
        invoice.line_items.append(item)
        
    # 4. Bank Details
    b_details = data.get("bank_details", {})
    if b_details:
        invoice.bank = BankDetails(
            bank_name=b_details.get("bank_name", "").strip(),
            account_number=b_details.get("account_number", "").strip(),
            ifsc_code=b_details.get("ifsc_code", "").strip(),
            swift_code=b_details.get("swift_code", "").strip()
        )
        
    # 5. Populate Tax Details dynamically from line items (guarantees perfect balancing!)
    tax_groups = {}
    for item in invoice.line_items:
        if item.gst_rate > 0:
            rate = item.gst_rate
            taxable = item.taxable_amount
            
            if is_igst:
                key = ("IGST", rate)
                tax_groups[key] = tax_groups.get(key, 0.0) + item.igst_amount
            else:
                key_c = ("CGST", rate / 2)
                tax_groups[key_c] = tax_groups.get(key_c, 0.0) + item.cgst_amount
                key_s = ("SGST", rate / 2)
                tax_groups[key_s] = tax_groups.get(key_s, 0.0) + item.sgst_amount
                
    for (t_type, r_val), t_amt in tax_groups.items():
        # Find total taxable base for this group
        grp_taxable = sum(item.taxable_amount for item in invoice.line_items 
                          if (item.gst_rate == r_val * 2 if t_type in ("CGST", "SGST") else item.gst_rate == r_val))
        invoice.tax_details.append(TaxDetail(
            tax_type=t_type,
            rate=r_val,
            taxable_amount=round(grp_taxable, 2),
            tax_amount=round(t_amt, 2)
        ))
        
    # 6. Recalculate totals to be absolutely balanced
    if not invoice.subtotal:
        invoice.subtotal = round(sum(item.taxable_amount for item in invoice.line_items), 2)
    invoice.total_tax = round(sum(td.tax_amount for td in invoice.tax_details), 2)
    
    # Re-infer grand total if needed to be bulletproof
    computed_total = round(invoice.subtotal + invoice.total_tax + invoice.round_off, 2)
    if abs(invoice.grand_total_float - computed_total) > 1.0:
        logger.warning(f"Vision API grand_total ({invoice.grand_total_float}) differed from computed ({computed_total}). Updating to computed for balancing.")
        invoice.grand_total = ExtractedField.high(f"{computed_total:.2f}", ExtractionSource.OCR)
        
    return invoice
