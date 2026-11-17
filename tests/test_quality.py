from pathlib import Path

from instruction_mvp.quality import evaluate
from instruction_mvp.response_contract import parse_response


VALID_DRAFT = """**ИНСТРУКЦИЯ**

Инструкция для операторов Отчёты

Кому: операторам

# Общие сведения

Инструкция описывает формирование отчёта в системе «Отчёты».

- Как сформировать отчёт

# Отчёты

Работа происходит во вкладке «Отчёты» (рисунок 1).

[СКРИНШОТ: вкладка «Отчёты»]

Рисунок 1 — Вкладка «Отчёты»

## Как сформировать отчёт

1. Нажмите «Сформировать».
2. Выберите период.
"""


def test_valid_response_contract():
    parsed = parse_response("STATUS: READY\n\n" + VALID_DRAFT)
    assert parsed.valid
    assert parsed.status == "READY"
    assert parsed.body == VALID_DRAFT.strip()


def test_need_info_rejects_demo_draft():
    parsed = parse_response("STATUS: NEED_INFO\n\n1. Кому нужна инструкция?\n\n---\n\nЧерновик")
    assert not parsed.valid
    assert "только нумерованные вопросы" in parsed.errors[0]


def test_attached_draft_is_critical_contract_violation():
    text = Path("inputs/test_run_draft.md").read_text(encoding="utf-8")
    report = evaluate(text)
    assert report.recommendation == "needs_revision"
    assert any(issue.code == "need_info_contract" for issue in report.critical_issues)


def test_valid_draft_ready_for_human_review():
    source = "Система «Отчёты». Вкладка «Отчёты». Кнопка «Сформировать»."
    report = evaluate(VALID_DRAFT, source)
    assert report.score == 100
    assert report.recommendation == "ready_for_human_review"


def test_mismatched_tasks_and_unsupported_url_are_critical():
    draft = VALID_DRAFT.replace("- Как сформировать отчёт", "- Как удалить отчёт")
    draft += "\n[Инструкция](https://example.com/unknown)\n"
    report = evaluate(draft, "«Отчёты» «Сформировать»")
    codes = {issue.code for issue in report.critical_issues}
    assert "tasks_mismatch" in codes
    assert "url_source" in codes
    assert report.recommendation == "needs_revision"


def test_revision_metrics():
    report = evaluate(VALID_DRAFT, "«Отчёты» «Сформировать»", VALID_DRAFT.replace("Нажмите", "Выберите"))
    assert report.change_ratio > 0
    assert 0 < report.similarity < 100


def test_empty_draft():
    report = evaluate("")
    assert report.score == 0
    assert report.critical_issues[0].code == "empty"
