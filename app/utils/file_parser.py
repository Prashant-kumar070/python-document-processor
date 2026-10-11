from pathlib import Path
from typing import Any

import docx
from docx.document import Document as DocumentObject
from docx.table import Table, _Cell
from docx.text.paragraph import Paragraph
from pypdf import PdfReader


def parse_pdf(path: str) -> list[dict[str, Any]]:
    reader = PdfReader(path)
    pages: list[dict[str, Any]] = []
    for page_number, page in enumerate(reader.pages, start=1):
        text = page.extract_text() or ""
        pages.append({"page_number": page_number, "text": text})
    return pages


def parse_docx(path: str) -> list[dict[str, Any]]:
    document = docx.Document(path)
    text_blocks: list[str] = []
    for section in document.sections:
        header_text = _extract_docx_blocks(section.header)
        if header_text:
            text_blocks.append(f"[HEADER]\n{header_text}")

    body_text = _extract_docx_blocks(document)
    if body_text:
        text_blocks.append(body_text)

    for section in document.sections:
        footer_text = _extract_docx_blocks(section.footer)
        if footer_text:
            text_blocks.append(f"[FOOTER]\n{footer_text}")

    return [{"page_number": 1, "text": "\n".join(text_blocks)}]


def _extract_docx_blocks(container: DocumentObject | _Cell) -> str:
    blocks: list[str] = []
    for block in container.iter_inner_content():
        if isinstance(block, Paragraph):
            paragraph_text = block.text.strip()
            if paragraph_text:
                blocks.append(paragraph_text)
        elif isinstance(block, Table):
            table_text = _extract_table(block)
            if table_text:
                blocks.append(table_text)
    return "\n".join(blocks)


def _extract_table(table: Table) -> str:
    rows: list[str] = []
    for row in table.rows:
        cells = [
            _extract_docx_blocks(cell).replace("\n", " / ").strip()
            for cell in row.cells
        ]
        if any(cells):
            rows.append("| " + " | ".join(cells) + " |")
    return "\n".join(rows)


def parse_document_text(path: str) -> list[dict[str, Any]]:
    file_path = Path(path)
    suffix = file_path.suffix.lower()
    if suffix == ".pdf":
        return parse_pdf(str(file_path))
    if suffix == ".docx":
        return parse_docx(str(file_path))
    raise ValueError("Unsupported file type")
