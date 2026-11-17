from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass
class ParsedResponse:
    status: str
    body: str
    questions: list[str] = field(default_factory=list)
    valid: bool = True
    errors: list[str] = field(default_factory=list)


def parse_response(text: str) -> ParsedResponse:
    clean = text.lstrip("﻿").strip()
    first_line, _, remainder = clean.partition("\n")
    first_line = first_line.strip()
    body = remainder.strip()

    if first_line not in {"STATUS: READY", "STATUS: NEED_INFO"}:
        return ParsedResponse(
            status="INVALID",
            body=clean,
            valid=False,
            errors=["Первая строка должна быть STATUS: READY или STATUS: NEED_INFO."],
        )

    if first_line == "STATUS: READY":
        errors = []
        if not body:
            errors.append("После STATUS: READY отсутствует текст инструкции.")
        elif "ИНСТРУКЦИЯ" not in body:
            errors.append("После STATUS: READY не найдена шапка инструкции.")
        return ParsedResponse("READY", body, valid=not errors, errors=errors)

    lines = [line.strip() for line in body.splitlines() if line.strip()]
    questions = [line for line in lines if re.match(r"^\d+\.\s+\S", line)]
    valid = bool(lines) and len(questions) == len(lines)
    errors = []
    if not lines:
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
