from __future__ import annotations
import re
from difflib import SequenceMatcher
from .models import Issue, Metric, QualityReport
from .response_contract import parse_response

WEIGHTS={"source":25,"structure":20,"tasks":15,"figures":15,"style":15,"readability":10}

def _metric(key,label,score,details):
    return Metric(key,label,round(max(0.,min(100.,score)),1),WEIGHTS[key],details)

def _section(text,heading):
    m=re.search(rf"(?im)^# {re.escape(heading)}\s*$",text)
    if not m:return ""
    n=re.search(r"(?m)^# ",text[m.end():])
    end=m.end()+n.start() if n else len(text)
    return text[m.end():end]

def _norm(value):
    return re.sub(r"[\s.]+"," ",value.strip().rstrip(".")).casefold()

def _structure(text,issues):
    checks={
      "строка ИНСТРУКЦИЯ":bool(re.search(r"(?im)^\**\s*ИНСТРУКЦИЯ\s*\**$",text)),
      "название Инструкция для":bool(re.search(r"(?im)^Инструкция для\s+\S",text)),
      "строка Кому":bool(re.search(r"(?im)^Кому:\s*\S",text)),
      "раздел Общие сведения":bool(re.search(r"(?im)^# Общие сведения\s*$",text)),
      "задача Как":bool(re.search(r"(?im)^## Как\s+\S",text))}
    missing=[k for k,v in checks.items() if not v]
    if missing:issues.append(Issue("structure_missing","Отсутствует: "+"; ".join(missing), "critical","structure"))
    passed=len(checks)-len(missing)
    return _metric("structure","Структура",passed/len(checks)*100,f"Выполнено {passed} из {len(checks)}")

def _tasks(text,issues):
    general=_section(text,"Общие сведения")
    left=[_norm(x) for x in re.findall(r"(?im)^\s*[-*]\s+(Как\s+.+?)\s*$",general)]
    right=[_norm(x) for x in re.findall(r"(?im)^##\s+(Как\s+.+?)\s*$",text)]
    matched=sum(1 for x in left if x in right)
    den=len(left)+len(right)
    score=2*matched/den*100 if den else 0.
    if not left or not right or left!=right and set(left)!=set(right):
        issues.append(Issue("tasks_mismatch",f"Список задач ({len(left)}) не совпадает с заголовками ({len(right)})","critical","tasks"))
    return _metric("tasks","Согласованность задач",score,f"В списке {len(left)}, заголовков {len(right)}")


def _figures(text,issues):
    numbers=[int(x) for x in re.findall(r"(?im)^\s*Рисунок\s+(\d+)\s+—\s+\S",text)]
    refs=[int(x) for x in re.findall(r"(?i)\(рисунок\s+(\d+)\)",text)]
    shots=len(re.findall(r"(?i)\[СКРИНШОТ:",text))
    unknown=sorted(set(refs)-set(numbers));unref=sorted(set(numbers)-set(refs))
    checks=[bool(numbers),numbers==list(range(1,len(numbers)+1)),shots==len(numbers) and bool(numbers),not unknown and not unref]
    if unknown:issues.append(Issue("figure_reference",f"Ссылки на несуществующие рисунки: {unknown}","critical","figures"))
    if numbers!=list(range(1,len(numbers)+1)):issues.append(Issue("figure_numbering",f"Нумерация рисунков: {numbers}","warning","figures"))
    if shots!=len(numbers):issues.append(Issue("figure_marker",f"Меток {shots}, подписей {len(numbers)}","warning","figures"))
    if unref:issues.append(Issue("figure_unreferenced",f"Рисунки без ссылок: {unref}","warning","figures"))
    if not numbers:issues.append(Issue("figure_absent","Не найдены рисунки и метки скриншотов","warning","figures"))
    return _metric("figures","Рисунки и скриншоты",sum(checks)/4*100,f"Рисунков {len(numbers)}, меток {shots}, ссылок {len(refs)}")

def _task_lines(text):
    result=[];active=False
    for line in text.splitlines():
        if re.match(r"^##\s+Как\s+",line,re.I):active=True;continue
        if re.match(r"^#{1,2}\s+",line):active=False
        if active and re.match(r"^\s*\d+\.\s+",line):result.append(line.strip())
    return result

def _style(text,issues):
    steps=_task_lines(text);bad=[]
    for step in steps:
        action=re.sub(r"^\d+\.\s+","",step)
        action=re.sub(r"^[*_«\"']+","",action)
        first=re.match(r"([А-Яа-яЁё-]+)",action)
        if not first or not first.group(1).casefold().endswith(("те","тесь")):bad.append(step)
    dashes=len(re.findall(r"(?<=[ \t])-(?=[ \t])",text))
    step_score=100. if not steps else (len(steps)-len(bad))/len(steps)*100
    dash_score=100. if not dashes else max(0.,100-dashes*20)
    if bad:issues.append(Issue("step_imperative",f"Шагов без повелительного глагола: {len(bad)} из {len(steps)}","warning","style"))
    if dashes:issues.append(Issue("dash",f"Коротких тире: {dashes}","warning","style"))
    return _metric("style","Язык и оформление",step_score*.6+dash_score*.4,f"Шагов {len(steps)}, коротких тире {dashes}")

def _readability(text,issues):
    sentences=[x.strip() for x in re.split(r"(?<=[.!?])\s+",text) if len(x.split())>=3]
    long=[x for x in sentences if len(x.split())>25]
    markers=re.findall(r"\[УТОЧНИТЬ:\s*[^\]]+\]",text,re.I)
    clarification=_section(text,"Нужно уточнить")
    ratio=len(long)/len(sentences) if sentences else 0.
    score=100-min(50,round(ratio*100))-min(30,len(markers)*5)
    if markers and not clarification.strip():
        score-=40;issues.append(Issue("clarification_missing","Есть [УТОЧНИТЬ], но нет блока «Нужно уточнить»","critical","readability"))
    if long:issues.append(Issue("long_sentences",f"Предложений длиннее 25 слов: {len(long)} из {len(sentences)}","warning","readability"))
    if markers:issues.append(Issue("unresolved",f"Меток [УТОЧНИТЬ]: {len(markers)}","warning","readability"))
    return _metric("readability","Читаемость и полнота",score,f"Длинных предложений {len(long)}, уточнений {len(markers)}"),len(markers)


def _source(text,source_text,issues):
    corpus=source_text.casefold()
    ui=sorted(set(re.findall(r"«([^«»\n]{1,100})»",text)))
    urls=sorted(set(x.rstrip(".,;:") for x in re.findall(r"https?://[^\s)\]>]+",text)))
    bad_ui=[x for x in ui if x.casefold() not in corpus]
    bad_urls=[x for x in urls if x.casefold() not in corpus]
    total=len(ui)+len(urls);confirmed=total-len(bad_ui)-len(bad_urls)
    if bad_ui:issues.append(Issue("ui_source","Названия UI не подтверждены: "+", ".join(f"«{x}»" for x in bad_ui),"critical","source"))
    if bad_urls:issues.append(Issue("url_source","Ссылки не подтверждены: "+", ".join(bad_urls),"critical","source"))
    return _metric("source","Подтверждённость источником",confirmed/total*100 if total else 100.,f"Подтверждено {confirmed} из {total}"),bad_ui,bad_urls

def _changes(old_text,new_text):
    old=old_text.splitlines();new=new_text.splitlines()
    matcher=SequenceMatcher(a=old,b=new,autojunk=False)
    matched=sum(x.size for x in matcher.get_matching_blocks());total=len(old)+len(new)
    change=1. if total==0 else 1-2*matched/total
    similarity=SequenceMatcher(None,old_text,new_text,autojunk=False).ratio()
    return round(change*100,1),round(similarity*100,1)


def evaluate(draft: str, source_text: str = "", previous_text: str | None = None) -> QualityReport:
    text = draft.strip()
    if not text:
        return QualityReport(0.0, "needs_revision", issues=[Issue("empty", "Черновик пуст", "critical")])
    change = similarity = None
    if previous_text is not None:
        change, similarity = _changes(previous_text, text)
    if text.startswith("STATUS: NEED_INFO"):
        parsed = parse_response(text)
        if parsed.valid:
            return QualityReport(0.0, "need_info", issues=[Issue("need_info", "Нужны ответы на вопросы", "info")], change_ratio=change, similarity=similarity)
        return QualityReport(0.0, "needs_revision", issues=[Issue("need_info_contract", error, "critical") for error in parsed.errors], change_ratio=change, similarity=similarity)
    if text.startswith("STATUS: READY"):
        parsed = parse_response(text)
        if not parsed.valid:
            return QualityReport(0.0, "needs_revision", issues=[Issue("ready_contract", error, "critical") for error in parsed.errors])
        text = parsed.body
    issues = []
    source, bad_ui, bad_urls = _source(text, source_text, issues)
    readability, unresolved = _readability(text, issues)
    metrics = [source, _structure(text, issues), _tasks(text, issues), _figures(text, issues), _style(text, issues), readability]
    score = round(sum(metric.score * metric.weight for metric in metrics) / 100, 1)
    if change is not None and change > 50:
        issues.append(Issue("large_revision", f"Изменено {change}% строк; проверьте объём правок", "warning"))
    ready = score >= 85 and not any(issue.severity == "critical" for issue in issues)
    return QualityReport(score, "ready_for_human_review" if ready else "needs_revision", metrics, issues, unresolved, bad_ui, bad_urls, change, similarity)
