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


def _resolve_key(entry: dict) -> str:
    # Ключ можно задать напрямую (не рекомендуется) или через имя переменной окружения.
    if entry.get("api_key"):
        return str(entry["api_key"]).strip()
    env_name = str(entry.get("api_key_env", "")).strip()
    return os.getenv(env_name, "").strip() if env_name else ""


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
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        try:
            config = LLMConfig(
                api_key=_resolve_key(entry),
                model=str(entry.get("model", "")).strip(),
                base_url=str(entry.get("base_url", "")).strip().rstrip("/"),
                timeout=int(entry.get("timeout", 120)),
            )
        except (TypeError, ValueError):
            continue
        name = str(entry.get("name", "")).strip() or "Без имени"
        agents.append(Agent(name=name, config=config, note=str(entry.get("note", "")).strip()))
    return agents
