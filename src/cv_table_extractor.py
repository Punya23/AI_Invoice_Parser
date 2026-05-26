"""
cv_table_extractor.py — Deterministic Computer Vision + OCR Table Extraction

This module solves the "column hallucination" problem in Tesseract.
Instead of feeding the whole page to OCR, we:
1. Use OpenCV to detect physical horizontal and vertical lines.
2. Intersect them to find individual table cells.
3. Sort cells into a perfect grid (rows and columns).
4. Run Tesseract *only* on the tightly cropped image of each cell.
5. Reconstruct the 2D array deterministically.
"""

import cv2
import numpy as np
import pytesseract
import logging
from PIL import Image

logger = logging.getLogger(__name__)


def extract_tables_from_image(img: Image.Image) -> list[list[list[str]]]:
    """
    Extract perfectly structured boxed tables from an image.
    Returns a list of tables, where each table is a 2D list of strings.
    """
    # Convert PIL Image to OpenCV format (numpy array)
    cv_img = np.array(img)
    if len(cv_img.shape) == 3 and cv_img.shape[2] == 3:
        gray = cv2.cvtColor(cv_img, cv2.COLOR_RGB2GRAY)
    else:
        gray = cv_img

    # 1. Binarization (Inverse Thresholding so lines are white, background is black)
    # Adaptive threshold works well for scanned documents with varying lighting
    thresh = cv2.adaptiveThreshold(
        gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY_INV, 15, 5
    )

    # 2. Line Detection via Morphology
    scale = 20 # Defines minimum length of a line to be considered part of a table
    
    # Horizontal lines
    horiz_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (gray.shape[1] // scale, 1))
    horizontal_lines = cv2.morphologyEx(thresh, cv2.MORPH_OPEN, horiz_kernel, iterations=2)
    
    # Vertical lines
    vert_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (1, gray.shape[0] // scale))
    vertical_lines = cv2.morphologyEx(thresh, cv2.MORPH_OPEN, vert_kernel, iterations=2)

    # Combine to create a grid mask
    table_mask = cv2.addWeighted(horizontal_lines, 0.5, vertical_lines, 0.5, 0.0)
    _, table_mask = cv2.threshold(table_mask, 50, 255, cv2.THRESH_BINARY)

    # 3. Find Contours (Cells)
    contours, hierarchy = cv2.findContours(table_mask, cv2.RETR_TREE, cv2.CHAIN_APPROX_SIMPLE)
    
    bounding_boxes = []
    for c in contours:
        x, y, w, h = cv2.boundingRect(c)
        # Filter out tiny artifacts and massive full-page bounding boxes
        if 20 < w < gray.shape[1] * 0.9 and 15 < h < gray.shape[0] * 0.5:
            bounding_boxes.append((x, y, w, h))

    if not bounding_boxes:
        return []

    # 4. Group Cells into Tables and Rows
    # Sort top-to-bottom, then left-to-right
    # A robust way is to cluster by Y-coordinate for rows
    bounding_boxes.sort(key=lambda b: (b[1], b[0]))
    
    rows = []
    current_row = [bounding_boxes[0]]
    current_y = bounding_boxes[0][1]
    
    for box in bounding_boxes[1:]:
        x, y, w, h = box
        # If the box's Y is within 10 pixels of the current row's Y, it belongs to the same row
        if abs(y - current_y) <= 15:
            current_row.append(box)
        else:
            rows.append(sorted(current_row, key=lambda b: b[0]))
            current_row = [box]
            current_y = y
            
    if current_row:
        rows.append(sorted(current_row, key=lambda b: b[0]))

    # Filter out rows that have too few columns (likely stray lines/noise, not a table)
    table_rows = [r for r in rows if len(r) >= 3]
    
    if not table_rows:
        return []

    # Assemble into a single logical table (for simplicity, we assume one major boxed table per page)
    # 5. Cell-by-Cell OCR
    table_data = []
    
    # Custom config to treat each cell as a single block of text
    custom_config = r'--oem 3 --psm 6'

    for row_idx, row_boxes in enumerate(table_rows):
        row_data = []
        for col_idx, (x, y, w, h) in enumerate(row_boxes):
            # Add a small padding inside the cell to avoid borders interfering with OCR
            pad = 3
            cx, cy, cw, ch = x + pad, y + pad, w - (2 * pad), h - (2 * pad)
            
            # Ensure coordinates are within bounds
            cx, cy = max(0, cx), max(0, cy)
            cw, ch = max(1, cw), max(1, ch)
            
            # Crop cell
            cell_img = gray[cy:cy+ch, cx:cx+cw]
            
            # Binarize cell for better OCR (Otsu)
            _, cell_bin = cv2.threshold(cell_img, 0, 255, cv2.THRESH_BINARY | cv2.THRESH_OTSU)
            
            # Convert back to PIL for pytesseract
            pil_cell = Image.fromarray(cell_bin)
            
            text = pytesseract.image_to_string(pil_cell, config=custom_config).strip()
            # Clean up trailing line breaks
            text = " ".join(text.split("\n"))
            row_data.append(text)
            
        table_data.append(row_data)

    # 6. Quality Gate: reject tables where most cells are empty
    #    (borders detected but per-cell OCR produced nothing — common on noisy scans)
    total_cells = sum(len(row) for row in table_data)
    non_empty_cells = sum(1 for row in table_data for cell in row if cell.strip())
    fill_ratio = non_empty_cells / total_cells if total_cells > 0 else 0

    if fill_ratio < 0.15:
        logger.debug(f"CV table rejected: {fill_ratio:.0%} fill rate ({non_empty_cells}/{total_cells} non-empty cells)")
        return []

    # Also reject tables with no recognizable header row
    header_keywords = {'description', 'particulars', 'qty', 'quantity', 'rate',
                       'amount', 'hsn', 'sac', 'item', 'sl', 'sr', 'total', 'unit', 'price'}
    has_header = False
    for row in table_data[:5]:  # Check first 5 rows for a header
        row_text = " ".join(cell.lower() for cell in row if cell)
        matches = sum(1 for kw in header_keywords if kw in row_text)
        if matches >= 2:
            has_header = True
            break

    if not has_header:
        logger.debug(f"CV table rejected: no recognizable header row found")
        return []

    logger.debug(f"CV Extracted Table: {len(table_data)} rows, ~{len(table_data[0]) if table_data else 0} cols, fill={fill_ratio:.0%}")
    return [table_data]

