from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from .knowledge import KnowledgeBase
from .models import SourceInput
from .response_contract import ParsedResponse, parse_response

MAX_PROMPT_CHARS=120000

class LLMError(RuntimeError):pass

@dataclass(frozen=True)
class LLMConfig:
    api_key: str
    model: str
    base_url: str="https://api.openai.com/v1"
    timeout: int=120
    @property
    def configured(self)->bool:return bool(self.api_key.strip() and self.model.strip())
    @classmethod
    def from_env(cls)->"LLMConfig":
        _load_dotenv()
        return cls(os.getenv("OPENAI_API_KEY",""),os.getenv("OPENAI_MODEL","gpt-4.1-mini"),os.getenv("OPENAI_BASE_URL","https://api.openai.com/v1").rstrip("/"),int(os.getenv("OPENAI_TIMEOUT","120")))

def _load_dotenv()->None:
    path=Path(__file__).resolve().parent.parent/".env"
    if not path.exists():return
    for raw in path.read_text(encoding="utf-8-sig").splitlines():
        line=raw.strip()
        if not line or line.startswith("#") or "=" not in line:continue
        key,value=line.split("=",1);key=key.strip();value=value.strip().strip('"').strip("'")
        if key and key not in os.environ:os.environ[key]=value

def _request(config: LLMConfig,messages: list[dict[str,str]],transport: Callable|None=None)->str:
    if not config.configured:raise LLMError("LLM не настроена: задайте OPENAI_API_KEY и OPENAI_MODEL в .env.")
    url=config.base_url if config.base_url.endswith("/chat/completions") else config.base_url+"/chat/completions"
    payload={"model":config.model,"messages":messages,"temperature":0.1}
    if transport is not None:return transport(url,payload,config.timeout)
    request=urllib.request.Request(url,data=json.dumps(payload,ensure_ascii=False).encode("utf-8"),headers={"Authorization":f"Bearer {config.api_key}","Content-Type":"application/json"},method="POST")
    try:
        with urllib.request.urlopen(request,timeout=config.timeout) as response:data=json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        detail=error.read().decode("utf-8",errors="replace")[:500].replace(config.api_key,"[REDACTED]")
        raise LLMError(f"API вернул HTTP {error.code}: {detail}") from error
    except (urllib.error.URLError,TimeoutError) as error:raise LLMError(f"Не удалось связаться с API: {error.reason if isinstance(error,urllib.error.URLError) else 'таймаут'}") from error
    except json.JSONDecodeError as error:raise LLMError("API вернул некорректный JSON.") from error
    try:return data["choices"][0]["message"]["content"]
    except (KeyError,IndexError,TypeError) as error:raise LLMError("В ответе API нет choices[0].message.content.") from error

def _check_prompt(system: str,user: str)->None:
    if len(system)+len(user)>MAX_PROMPT_CHARS:raise LLMError("Материалы превышают 120 000 символов. Сократите ТЗ или базу знаний.")


def generate_instruction(source: SourceInput, kb: KnowledgeBase, config: LLMConfig, transport: Callable | None = None) -> ParsedResponse:
    missing = source.missing_required() + (["База знаний: " + item for item in kb.missing()])
    if missing:
        raise ValueError("Заполните: " + "; ".join(missing))
    system = kb.system_prompt + kb.prompt_context()
    user = "Сформируй инструкцию строго по переданным правилам.\n\n" + source.as_source_text()
    _check_prompt(system, user)
    raw = _request(config, [{"role": "system", "content": system}, {"role": "user", "content": user}], transport)
    return parse_response(raw)


def revise_instruction(source: SourceInput, kb: KnowledgeBase, current_draft: str, comments: str, config: LLMConfig, transport: Callable | None = None) -> ParsedResponse:
    if not current_draft.strip():
        raise ValueError("Нет текущего черновика для доработки.")
    if not comments.strip():
        raise ValueError("Добавьте хотя бы одно замечание.")
    if kb.missing():
        raise ValueError("База знаний неполна: " + "; ".join(kb.missing()))
    revision_source = SourceInput(**{**source.__dict__, "comments": comments})
    system = kb.system_prompt + kb.prompt_context()
    user = (
        "Выполни режим доработки. Примени только перечисленные замечания, "
        "остальной текст сохрани без изменений. Верни полный документ.\n\n"
        + revision_source.as_source_text(previous_draft=current_draft)
    )
    _check_prompt(system, user)
    raw = _request(config, [{"role": "system", "content": system}, {"role": "user", "content": user}], transport)
    return parse_response(raw)
