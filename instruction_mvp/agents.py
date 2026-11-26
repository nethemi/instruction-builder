from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

from .llm import LLMConfig, _load_dotenv

AGENTS_PATH = Path(__file__).resolve().parent.parent / "agents.json"


@dataclass(frozen=True)
class Agent:
    """Подключаемый агент: имя для списка + готовая конфигурация LLM."""

    name: str
    config: LLMConfig
    note: str = ""

    @property
    def configured(self) -> bool:
        return self.config.configured


def _resolve_key(entry: dict, secrets: dict | None = None) -> str:
    # Ключ можно задать напрямую (не рекомендуется), через имя переменной окружения
    # или через имя ключа в секретах Streamlit (api_key_secret).
    if entry.get("api_key"):
        return str(entry["api_key"]).strip()
    secret_name = str(entry.get("api_key_secret", "")).strip()
    if secret_name and secrets and secret_name in secrets:
        return str(secrets[secret_name]).strip()
    env_name = str(entry.get("api_key_env", "")).strip()
    return os.getenv(env_name, "").strip() if env_name else ""


_PLACEHOLDER_RE = __import__("re").compile(r"\{([A-Za-z0-9_]+)\}")


def _collect_secrets() -> dict[str, str]:
    """Секреты из окружения (.env) и, если приложение работает под Streamlit, из st.secrets."""
    secrets = {key: str(value) for key, value in os.environ.items()}
    try:
        import streamlit as st  # noqa: PLC0415 — опциональная зависимость

        for key, value in st.secrets.items():
            if isinstance(value, str):
                secrets[key] = value
    except Exception:  # noqa: BLE001 — вне Streamlit st.secrets недоступен
        pass
    return secrets


def _substitute(value, secrets: dict[str, str]):
    """Рекурсивно заменяет {ИМЯ_СЕКРЕТА} в строках значения на значения из secrets."""
    if isinstance(value, str):
        return _PLACEHOLDER_RE.sub(lambda match: secrets.get(match.group(1), match.group(0)), value)
    if isinstance(value, dict):
        return {key: _substitute(item, secrets) for key, item in value.items()}
    if isinstance(value, list):
        return [_substitute(item, secrets) for item in value]
    return value


def agents_from_entries(entries: list, secrets: dict | None = None) -> list[Agent]:
    """Превращает список словарей-записей в агентов. Битые записи пропускаются.

    В любой строке можно использовать плейсхолдеры {ИМЯ_СЕКРЕТА} — они заменяются
    на значения из secrets (окружение / st.secrets Streamlit).
    """
    secrets = secrets if secrets is not None else _collect_secrets()
    result: list[Agent] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        try:
            entry = _substitute(entry, secrets)
            extra_headers = entry.get("extra_headers", {})
            extra_body = entry.get("extra_body", {})
            config = LLMConfig(
                api_key=_resolve_key(entry, secrets),
                model=str(entry.get("model", "")).strip(),
                base_url=str(entry.get("base_url", "")).strip().rstrip("/"),
                timeout=int(entry.get("timeout", 120)),
                auth_scheme=str(entry.get("auth_scheme", "Bearer")).strip() or "Bearer",
                api=str(entry.get("api", "chat_completions")).strip() or "chat_completions",
                extra_headers=extra_headers if isinstance(extra_headers, dict) else {},
                extra_body=extra_body if isinstance(extra_body, dict) else {},
            )
        except (TypeError, ValueError):
            continue
        name = str(entry.get("name", "")).strip() or "Без имени"
        result.append(Agent(name=name, config=config, note=str(entry.get("note", "")).strip()))
    return result


def load_agents(path: Path | None = None) -> list[Agent]:
    """Возвращает список агентов: сначала «По умолчанию (.env)», затем записи из agents.json.

    Битые записи пропускаются, файл отсутствует — работает только дефолтный агент.
    """
    _load_dotenv()
    agents = [Agent(name="По умолчанию (.env)", config=LLMConfig.from_env())]
    path = path or AGENTS_PATH
    if not path.exists():
        return agents
    try:
        raw = json.loads(path.read_text(encoding="utf-8-sig"))
    except (json.JSONDecodeError, OSError):
        return agents
    entries = raw if isinstance(raw, list) else []
    return agents + agents_from_entries(entries)
