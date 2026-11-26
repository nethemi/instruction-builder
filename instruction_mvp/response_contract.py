from __future__ import annotations

import re
from dataclasses import dataclass, field

_STATUS_RE = re.compile(r"^\**\s*STATUS\s*[:：]\s*(READY|NEED[ _-]?INFO)\s*\**$", re.IGNORECASE)
_MAX_SCAN_LINES = 10  # ищем строку статуса среди первых строк ответа


@dataclass
class ParsedResponse:
    status: str
    body: str
    questions: list[str] = field(default_factory=list)
    valid: bool = True
    errors: list[str] = field(default_factory=list)


def parse_response(text: str) -> ParsedResponse:
    clean = text.lstrip("﻿").strip()
    # Снимаем кодовые fences: ``` / ```markdown / ```text и т.п.
    fence = re.match(r"^```[a-zA-Z]*\s*\n", clean)
    if fence:
        clean = clean[fence.end():]
        clean = re.sub(r"\n```\s*$", "", clean).strip()

    lines = clean.splitlines()
    status_index = None
    status = None
    for index, line in enumerate(lines[:_MAX_SCAN_LINES]):
        match = _STATUS_RE.match(line.strip())
        if match:
            status_index = index
            status = "READY" if match.group(1).upper() == "READY" else "NEED_INFO"
            break

    if status is None:
        return ParsedResponse(
            status="INVALID",
            body=clean,
            valid=False,
            errors=["Первая строка должна быть STATUS: READY или STATUS: NEED_INFO."],
        )

    body = "\n".join(lines[status_index + 1:]).strip()

    if status == "READY":
        errors = []
        if not body:
            errors.append("После STATUS: READY отсутствует текст инструкции.")
        elif "ИНСТРУКЦИЯ" not in body:
            errors.append("После STATUS: READY не найдена шапка инструкции.")
        return ParsedResponse("READY", body, valid=not errors, errors=errors)

    body_lines = [line.strip().strip("*").strip() for line in body.splitlines() if line.strip()]
    questions = [line for line in body_lines if re.match(r"^\d+\.\s+\S", line)]
    valid = bool(body_lines) and len(questions) == len(body_lines)
    errors = []
    if not body_lines:
        errors.append("После STATUS: NEED_INFO отсутствуют вопросы.")
    elif not valid:
        errors.append(
            "При STATUS: NEED_INFO разрешены только нумерованные вопросы; "
            "черновик, пояснения и разделители запрещены."
        )
    return ParsedResponse(
        "NEED_INFO",
        body,
        questions=questions,
        valid=valid,
        errors=errors,
    )
