from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass, field
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
    # Режим вызова: "chat_completions" (OpenAI-совместимый, сообщения) или
    # "responses" (Responses API: prompt id + tools, input — строка).
    api: str="chat_completions"
    # Схема авторизации: "Bearer" (OpenAI, IAM-токен Яндекса) или "Api-Key" (API-ключ Яндекса).
    auth_scheme: str="Bearer"
    # Дополнительные заголовки, например {"x-folder-id": "<folder_id>"} или {"OpenAI-Project": "<folder_id>"}.
    extra_headers: dict[str,str]=field(default_factory=dict)
    # Дополнительные поля тела запроса: для responses-режима здесь передаются
    # prompt id, tools и прочие параметры агента.
    extra_body: dict[str,Any]=field(default_factory=dict)
    @property
    def configured(self)->bool:
        # В responses-режиме модель не нужна: её несёт сохранённый промпт в облаке.
        has_model=self.model.strip() or self.api=="responses"
        return bool(self.api_key.strip() and has_model)
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

def _headers(config: LLMConfig)->dict[str,str]:
    headers={"Authorization":f"{config.auth_scheme} {config.api_key}","Content-Type":"application/json"}
    headers.update(config.extra_headers)
    return headers

def _post_json(url: str,payload: dict[str,Any],headers: dict[str,str],timeout: int,api_key: str)->dict[str,Any]:
    request=urllib.request.Request(url,data=json.dumps(payload,ensure_ascii=False).encode("utf-8"),headers=headers,method="POST")
    try:
        with urllib.request.urlopen(request,timeout=timeout) as response:return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        detail=error.read().decode("utf-8",errors="replace")[:500].replace(api_key,"[REDACTED]")
        raise LLMError(f"API вернул HTTP {error.code}: {detail}") from error
    except (urllib.error.URLError,TimeoutError) as error:raise LLMError(f"Не удалось связаться с API: {error.reason if isinstance(error,urllib.error.URLError) else 'таймаут'}") from error
    except json.JSONDecodeError as error:raise LLMError("API вернул некорректный JSON.") from error

def _request(config: LLMConfig,messages: list[dict[str,str]],transport: Callable|None=None)->str:
    if not config.configured:raise LLMError("LLM не настроена: задайте OPENAI_API_KEY и OPENAI_MODEL в .env.")
    url=config.base_url if config.base_url.endswith("/chat/completions") else config.base_url+"/chat/completions"
    payload: dict[str,Any]={"model":config.model,"messages":messages,"temperature":0.1}
    payload.update(config.extra_body)
    if transport is not None:return transport(url,payload,config.timeout)
    data=_post_json(url,payload,_headers(config),config.timeout,config.api_key)
    try:return data["choices"][0]["message"]["content"]
    except (KeyError,IndexError,TypeError) as error:raise LLMError("В ответе API нет choices[0].message.content.") from error

def _extract_responses_text(data: dict[str,Any])->str:
    texts=[]
    for item in data.get("output",[]) if isinstance(data,dict) else []:
        if not isinstance(item,dict):continue
        if item.get("type")=="message":
            for content in item.get("content",[]):
                if isinstance(content,dict) and content.get("type") in ("output_text","text") and content.get("text"):
                    texts.append(content["text"])
        # Некоторые совместимые API кладут текст прямо в output_text-элемент
        elif item.get("type") in ("output_text","text") and item.get("text"):
            texts.append(item["text"])
    return "\n".join(texts)

def _request_responses(config: LLMConfig,user_text: str,transport: Callable|None=None)->str:
    if not config.configured:raise LLMError("Агент не настроен: задайте API-ключ в .env.")
    url=config.base_url if config.base_url.endswith("/responses") else config.base_url+"/responses"
    payload: dict[str,Any]={"input":user_text}
    payload.update(config.extra_body)
    if transport is not None:return transport(url,payload,config.timeout)
    data=_post_json(url,payload,_headers(config),config.timeout,config.api_key)
    text=_extract_responses_text(data)
    if not text:
        status=data.get("status") if isinstance(data,dict) else None
        error=data.get("error") if isinstance(data,dict) else None
        output_types=[item.get("type") for item in data.get("output",[]) if isinstance(item,dict)] if isinstance(data,dict) else []
        raise LLMError(
            "В ответе Responses API нет текстового вывода. "
            f"status={status!r}, error={error!r}, вывод: {output_types or 'пусто'}. "
            "Проверьте, что промпт агента в облаке возвращает STATUS: READY/NEED_INFO."
        )
    return text

def _check_prompt(system: str,user: str)->None:
    if len(system)+len(user)>MAX_PROMPT_CHARS:raise LLMError("Материалы превышают 120 000 символов. Сократите ТЗ или базу знаний.")


def generate_instruction(source: SourceInput, kb: KnowledgeBase, config: LLMConfig, transport: Callable | None = None) -> ParsedResponse:
    if config.api=="responses":
        # У агента в облаке свой промпт и инструменты: отправляем только вводные.
        missing=source.missing_required()
        if missing:raise ValueError("Заполните: "+"; ".join(missing))
        user=source.as_source_text()
        _check_prompt("",user)
        return parse_response(_request_responses(config,user,transport))
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
    revision_source = SourceInput(**{**source.__dict__, "comments": comments})
    if config.api=="responses":
        user=("Выполни режим доработки. Примени только перечисленные замечания, "
              "остальной текст сохрани без изменений. Верни полный документ.\n\n"
              +revision_source.as_source_text(previous_draft=current_draft))
        _check_prompt("",user)
        return parse_response(_request_responses(config,user,transport))
    if kb.missing():
        raise ValueError("База знаний неполна: " + "; ".join(kb.missing()))
    system = kb.system_prompt + kb.prompt_context()
    user = (
        "Выполни режим доработки. Примени только перечисленные замечания, "
        "остальной текст сохрани без изменений. Верни полный документ.\n\n"
        + revision_source.as_source_text(previous_draft=current_draft)
    )
    _check_prompt(system, user)
    raw = _request(config, [{"role": "system", "content": system}, {"role": "user", "content": user}], transport)
    return parse_response(raw)
