from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

MAX_FILE_BYTES=2*1024*1024

@dataclass(frozen=True)
class KnowledgeBase:
    system_prompt: str
    rules: str
    template: str
    example: str
    def missing(self)->list[str]:
        labels={"system_prompt":"системный промпт","rules":"правила","template":"шаблон","example":"пример"}
        return [label for name,label in labels.items() if not getattr(self,name).strip()]
    def prompt_context(self)->str:
        return f"\n\n# ПРАВИЛА\n{self.rules}\n\n# ШАБЛОН\n{self.template}\n\n# ПРИМЕР — только стиль, не источник фактов\n{self.example}"

def load_internal_kb(root: Path|None=None)->KnowledgeBase:
    base=root or Path(__file__).resolve().parent.parent/"knowledge"/"internal"
    return KnowledgeBase(**{name:(base/f"{name}.md").read_text(encoding="utf-8") for name in ("system_prompt","rules","template","example")})

def kb_from_uploads(files: Mapping[str,object])->KnowledgeBase:
    values={}
    for name in ("system_prompt","rules","template","example"):
        file=files.get(name)
        if file is None:values[name]="";continue
        data=file.getvalue()
        if len(data)>MAX_FILE_BYTES:raise ValueError(f"Файл {file.name} больше 2 МБ.")
        try:values[name]=data.decode("utf-8-sig")
        except UnicodeDecodeError as error:raise ValueError(f"Файл {file.name} должен быть в UTF-8.") from error
    return KnowledgeBase(**values)
