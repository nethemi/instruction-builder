import json
from pathlib import Path

from docx import Document

from instruction_mvp.exporters import docx_bytes, markdown_bytes
from instruction_mvp.history import append_metrics, load_metrics, summarise
from instruction_mvp.knowledge import KnowledgeBase
from instruction_mvp.llm import LLMConfig, generate_instruction, revise_instruction
from instruction_mvp.models import SourceInput
from instruction_mvp.quality import evaluate
from test_quality import VALID_DRAFT


KB = KnowledgeBase("Системный промпт", "Правила", "Шаблон", "Пример")
CONFIG = LLMConfig("test-key", "test-model")
SOURCE = SourceInput("internal", "Операторы", "Вкладка «Отчёты»", "Формирование отчёта", "1. Нажмите «Сформировать».", "Контекст")


def test_generation_uses_transport_and_parses_response():
    calls = []
    def transport(url, payload, timeout):
        calls.append((url, payload, timeout))
        return "STATUS: READY\n\n" + VALID_DRAFT
    result = generate_instruction(SOURCE, KB, CONFIG, transport)
    assert result.valid
    assert calls[0][0].endswith("/chat/completions")
    assert "Пример" in calls[0][1]["messages"][0]["content"]


def test_revision_requires_comments():
    try:
        revise_instruction(SOURCE, KB, VALID_DRAFT, "", CONFIG, lambda *_: "")
    except ValueError as error:
        assert "замечание" in str(error)
    else:
        raise AssertionError("Пустые замечания должны отклоняться")


def test_docx_and_markdown_export(tmp_path):
    data = docx_bytes(VALID_DRAFT)
    path = tmp_path / "instruction.docx"
    path.write_bytes(data)
    document = Document(path)
    text = "\n".join(paragraph.text for paragraph in document.paragraphs)
    assert "ИНСТРУКЦИЯ" in text
    assert "Как сформировать отчёт" in text
    assert markdown_bytes(VALID_DRAFT).decode("utf-8").startswith("**ИНСТРУКЦИЯ**")


def test_history_skips_bad_json_and_stores_no_document(tmp_path):
    path = tmp_path / "metrics.jsonl"
    report = evaluate(VALID_DRAFT, "«Отчёты» «Сформировать»")
    append_metrics(path, report, "internal", "approved", 12.5)
    with path.open("a", encoding="utf-8") as stream:
        stream.write("not-json\n")
    records = load_metrics(path)
    assert len(records) == 1
    assert "draft" not in records[0]
    assert "context" not in records[0]
    assert summarise(records)["count"] == 1
