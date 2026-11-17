from __future__ import annotations
import re
from io import BytesIO
from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_COLOR_INDEX
from docx.shared import Pt


def markdown_bytes(text: str) -> bytes:
    return text.strip().encode("utf-8") + b"\n"


def _inline(paragraph, text: str) -> None:
    pattern = re.compile(r"(\*\*.*?\*\*|\[УТОЧНИТЬ:[^\]]+\]|\[[^\]]+\]\([^)]+\))")
    for part in pattern.split(text):
        if not part:
            continue
        if part.startswith("**") and part.endswith("**"):
            paragraph.add_run(part[2:-2]).bold = True
        elif part.startswith("[УТОЧНИТЬ:"):
            paragraph.add_run(part).font.highlight_color = WD_COLOR_INDEX.YELLOW
        else:
            link = re.fullmatch(r"\[([^\]]+)\]\(([^)]+)\)", part)
            paragraph.add_run(f"{link.group(1)} ({link.group(2)})" if link else part)


def docx_bytes(text: str) -> bytes:
    document = Document()
    style = document.styles["Normal"]
    style.font.name = "Arial"
    style.font.size = Pt(11)
    for raw in text.strip().splitlines():
        line = raw.rstrip()
        if not line.strip() or re.fullmatch(r"\s*-{3,}\s*", line):
            continue
        heading = re.match(r"^(#{1,3})\s+(.+)$", line)
        if heading:
            document.add_heading(heading.group(2), level=min(len(heading.group(1)), 3))
            continue
        if line.strip() == "**ИНСТРУКЦИЯ**":
            paragraph = document.add_paragraph()
            paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
            run = paragraph.add_run("ИНСТРУКЦИЯ")
            run.bold = True
            run.font.size = Pt(16)
            continue
        bullet = re.match(r"^\s*[-*]\s+(.+)$", line)
        numbered = re.match(r"^\s*\d+\.\s+(.+)$", line)
        if bullet or numbered:
            paragraph = document.add_paragraph(style="List Bullet" if bullet else "List Number")
            _inline(paragraph, (bullet or numbered).group(1))
            continue
        paragraph = document.add_paragraph()
        if line.lstrip().startswith("[СКРИНШОТ:"):
            _inline(paragraph, line.strip())
            paragraph.runs[0].italic = True
            continue
        _inline(paragraph, line.strip())
        if re.match(r"^Рисунок\s+\d+\s+—", line.strip()):
            for run in paragraph.runs:
                run.bold = True
    output = BytesIO()
    document.save(output)
    return output.getvalue()
