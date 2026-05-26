"""
text_extractor.py — Digital PDF Text & Table Extraction

Two-tier extraction for digital PDFs:
  Tier 1: pdfplumber extract_tables() — works on bordered tables
  Tier 2: Positional word clustering — fallback for borderless tables

Calibrated against actual Accuron invoices:
- CIEL HR: bordered table → Tier 1 works
- Vault Infosec: bordered table → Tier 1 works
- Green Clean: borderless (Tally-style) → needs Tier 2
- AWS, INUBE: no tables, pure text → regex parsing only
"""

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import pdfplumber

logger = logging.getLogger(__name__)

# ─── Positional Word Clustering Constants ─────────────────────────────────────
ROW_TOLERANCE = 5       # pts: words within 5pt Y-distance → same row
COLUMN_TOLERANCE = 15   # pts: X-positions within 15pt → same column


# ─── Data Structures ──────────────────────────────────────────────────────────

@dataclass
class PageContent:
    page_number: int
    text: str = ""
    tables: list = field(default_factory=list)  # list[list[list[str]]]
    table_source: str = "none"  # "pdfplumber" | "clustered" | "none"
    word_count: int = 0


@dataclass
class DocumentContent:
    pdf_path: str
    pages: list[PageContent] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)

    @property
    def full_text(self) -> str:
        return "\n\n".join(p.text for p in self.pages)

    @property
    def all_tables(self) -> list:
        tables = []
        for page in self.pages:
            tables.extend(page.tables)
        return tables


# ─── Word-Level Clustering ────────────────────────────────────────────────────

@dataclass
class Word:
    text: str
    x0: float
    y0: float
    x1: float
    y1: float


def _extract_words(page) -> list[Word]:
    """Extract words with bounding boxes from a pdfplumber page."""
    raw = page.extract_words(x_tolerance=3, y_tolerance=3) or []
    return [
        Word(text=w["text"], x0=float(w["x0"]), y0=float(w["top"]),
             x1=float(w["x1"]), y1=float(w["bottom"]))
        for w in raw if w["text"].strip()
    ]


def _cluster_rows(words: list[Word]) -> list[list[Word]]:
    """Group words into rows by Y-coordinate proximity."""
    if not words:
        return []

    sorted_words = sorted(words, key=lambda w: (w.y0, w.x0))
    rows: list[list[Word]] = []
    current_row = [sorted_words[0]]
    current_y = sorted_words[0].y0

    for word in sorted_words[1:]:
        if abs(word.y0 - current_y) <= ROW_TOLERANCE:
            current_row.append(word)
        else:
            rows.append(sorted(current_row, key=lambda w: w.x0))
            current_row = [word]
            current_y = word.y0

    if current_row:
        rows.append(sorted(current_row, key=lambda w: w.x0))
    return rows


def _find_columns(rows: list[list[Word]]) -> list[float]:
    """Find column boundaries from X-positions of all words."""
    all_x: list[float] = []
    for row in rows:
        for w in row:
            all_x.append(w.x0)
    if not all_x:
        return []

    all_x.sort()
    clusters: list[list[float]] = [[all_x[0]]]
    for x in all_x[1:]:
        if x - clusters[-1][-1] <= COLUMN_TOLERANCE:
            clusters[-1].append(x)
        else:
            clusters.append([x])

    return [min(c) for c in clusters]


def _assign_column(word: Word, boundaries: list[float]) -> int:
    """Find which column a word belongs to."""
    assigned = 0
    for i, b in enumerate(boundaries):
        if word.x0 >= b - COLUMN_TOLERANCE:
            assigned = i
    return assigned


def _build_grid(rows: list[list[Word]], boundaries: list[float]) -> list[list[str]]:
    """Build a 2D table grid from clustered rows + columns."""
    n_cols = len(boundaries)
    grid = []
    for row in rows:
        cells: list[list[str]] = [[] for _ in range(n_cols)]
        for word in row:
            col = _assign_column(word, boundaries)
            cells[col].append(word.text)
        grid.append([" ".join(c).strip() for c in cells])
    return grid


def cluster_page_tables(page, min_rows: int = 3, min_cols: int = 3) -> list[list[list[str]]]:
    """
    Extract tables via positional word clustering.
    Used as fallback when pdfplumber.extract_tables() returns nothing.
    """
    words = _extract_words(page)
    if not words:
        return []

    rows = _cluster_rows(words)
    if len(rows) < min_rows:
        return []

    boundaries = _find_columns(rows)
    if len(boundaries) < min_cols:
        return []

    grid = _build_grid(rows, boundaries)
    logger.debug(f"Clustered table: {len(grid)} rows × {len(boundaries)} cols")
    return [grid]


# ─── Main Extraction ──────────────────────────────────────────────────────────

def extract_document(pdf_path: str) -> DocumentContent:
    """
    Extract text and tables from a digital PDF.
    
    For each page:
    1. Extract full text via pdfplumber
    2. Try bordered table extraction (pdfplumber.extract_tables)
    3. If no tables found, try positional word clustering
    """
    doc = DocumentContent(pdf_path=pdf_path)

    with pdfplumber.open(pdf_path) as pdf:
        doc.metadata = pdf.metadata or {}

        for i, page in enumerate(pdf.pages, start=1):
            pc = PageContent(page_number=i)

            # 1. Text extraction
            try:
                pc.text = page.extract_text(x_tolerance=3, y_tolerance=3) or ""
            except Exception as e:
                logger.warning(f"Page {i} text extraction failed: {e}")
                pc.text = ""

            pc.word_count = len(pc.text.split())

            # 2. Table extraction — Tier 1: Camelot (lattice & stream)
            try:
                import camelot
                import warnings
                # Suppress camelot warnings
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore")
                    # First try lattice (bordered tables)
                    camelot_tables = camelot.read_pdf(pdf_path, pages=str(i), flavor='lattice')
                    if not camelot_tables:
                        # Try stream (borderless tables)
                        camelot_tables = camelot.read_pdf(pdf_path, pages=str(i), flavor='stream')
                
                valid_tables = []
                for t in camelot_tables:
                    df = t.df
                    # Convert df to list of lists
                    table_list = df.values.tolist()
                    if len(table_list) >= 2 and any(any(str(cell).strip() for cell in row) for row in table_list):
                        valid_tables.append([[str(c) for c in row] for row in table_list])
                
                if valid_tables:
                    pc.tables = valid_tables
                    pc.table_source = "camelot"
                    logger.debug(f"Page {i}: {len(valid_tables)} tables found via Camelot")
            except Exception as e:
                logger.warning(f"Page {i} Camelot table extraction failed: {e}")

            # 2.5. Tier 1.5: pdfplumber.extract_tables() fallback if Camelot failed
            if not pc.tables:
                try:
                    plumber_tables = page.extract_tables() or []
                    valid_tables = []
                    for table in plumber_tables:
                        if table and len(table) >= 2:
                            cleaned = [[str(cell or "") for cell in row] for row in table]
                            # Quality gate: reject tables where most cells are empty
                            total_cells = sum(len(r) for r in cleaned)
                            non_empty = sum(1 for r in cleaned for c in r if c.strip())
                            fill_ratio = non_empty / total_cells if total_cells > 0 else 0
                            if fill_ratio >= 0.15:
                                valid_tables.append(cleaned)
                    if valid_tables:
                        pc.tables = valid_tables
                        pc.table_source = "pdfplumber"
                        logger.debug(f"Page {i}: {len(valid_tables)} tables found via pdfplumber fallback")
                except Exception as e:
                    logger.warning(f"Page {i} pdfplumber table extraction failed: {e}")

            # 3. Table extraction — Tier 2: word clustering fallback
            if not pc.tables and pc.word_count > 20:
                try:
                    clustered = cluster_page_tables(page)
                    if clustered:
                        pc.tables = clustered
                        pc.table_source = "clustered"
                        logger.debug(f"Page {i}: table reconstructed via word clustering")
                except Exception as e:
                    logger.warning(f"Page {i} clustering failed: {e}")

            doc.pages.append(pc)

    logger.info(f"Extracted {len(doc.pages)} pages from {Path(pdf_path).name}")
    return doc
