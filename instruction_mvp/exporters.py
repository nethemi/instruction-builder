from __future__ import annotations

import datetime
import re
from io import BytesIO

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_COLOR_INDEX
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Mm, Pt, RGBColor

BLACK = RGBColor(0, 0, 0)
FOOTER_TEXT = "© {year} One Company Inc. Все авторские права защищены законом."


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


def _setup_page(document: Document) -> None:
    """A4, поля: верх 1 см, остальные 2 см."""
    section = document.sections[0]
    section.page_width = Mm(210)
    section.page_height = Mm(297)
    section.top_margin = Cm(1)
    section.bottom_margin = Cm(2)
    section.left_margin = Cm(2)
    section.right_margin = Cm(2)


def _setup_base_style(document: Document) -> None:
    style = document.styles["Normal"]
    style.font.name = "Arial"
    style.font.size = Pt(12)
    style.font.color.rgb = BLACK


def _add_page_number(paragraph) -> None:
    """Поле номера страницы (PAGE) в колонтитуле."""
    run = paragraph.add_run()
    begin = OxmlElement("w:fldChar")
    begin.set(qn("w:fldCharType"), "begin")
    instr = OxmlElement("w:instrText")
    instr.set(qn("xml:space"), "preserve")
    instr.text = "PAGE"
    end = OxmlElement("w:fldChar")
    end.set(qn("w:fldCharType"), "end")
    run._r.append(begin)
    run._r.append(instr)
    run._r.append(end)
    run.font.name = "Montserrat"
    run.font.size = Pt(8)


def _setup_footer(document: Document) -> None:
    """Колонтитул: копирайт слева, номер страницы справа, Montserrat 8 pt."""
    paragraph = document.sections[0].footer.paragraphs[0]
    copyright_run = paragraph.add_run(FOOTER_TEXT.format(year=datetime.date.today().year))
    copyright_run.font.name = "Montserrat"
    copyright_run.font.size = Pt(8)
    copyright_run.font.color.rgb = BLACK
    paragraph.add_run("\t")
    _add_page_number(paragraph)


def _add_heading(document: Document, text: str, level: int, is_title: bool) -> None:
    """Заголовки строго чёрные: название документа — 20 pt bold отдельным параграфом,
    H1 — 18 pt bold, H2 — 16 pt, H3 — 14 pt."""
    paragraph = document.add_paragraph()
    run = paragraph.add_run(text)
    run.font.color.rgb = BLACK
    if is_title:
        run.font.size = Pt(20)
        run.bold = True
    elif level == 1:
        run.font.size = Pt(18)
        run.bold = True
    elif level == 2:
        run.font.size = Pt(16)
    else:
        run.font.size = Pt(14)


def docx_bytes(text: str) -> bytes:
    document = Document()
    _setup_page(document)
    _setup_base_style(document)
    _setup_footer(document)
    title_pending = True  # первый заголовок первого уровня — название документа
    for raw in text.strip().splitlines():
        line = raw.rstrip()
        if not line.strip() or re.fullmatch(r"\s*-{3,}\s*", line):
            continue
        heading = re.match(r"^(#{1,3})\s+(.+)$", line)
        if heading:
            level = len(heading.group(1))
            _add_heading(document, heading.group(2), level, is_title=(title_pending and level == 1))
            if level == 1:
                title_pending = False
            continue
        if line.strip() == "**ИНСТРУКЦИЯ**":
            paragraph = document.add_paragraph()
            paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
            run = paragraph.add_run("ИНСТРУКЦИЯ")
            run.bold = True
            run.font.size = Pt(16)
            run.font.color.rgb = BLACK
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
