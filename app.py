from __future__ import annotations
import difflib
import time
from pathlib import Path
import streamlit as st
from instruction_mvp.exporters import docx_bytes, markdown_bytes
from instruction_mvp.history import append_metrics, load_metrics, summarise
from instruction_mvp.knowledge import KnowledgeBase, kb_from_uploads, load_internal_kb
from instruction_mvp.llm import LLMConfig, LLMError, generate_instruction, revise_instruction
from instruction_mvp.models import QualityReport, SourceInput
from instruction_mvp.quality import evaluate

ROOT=Path(__file__).resolve().parent
HISTORY_PATH=ROOT/"data"/"metrics.jsonl"
st.set_page_config(page_title="Конструктор инструкций",layout="wide")
st.markdown("""<style>
.block-container{max-width:1320px;padding-top:2rem;padding-bottom:4rem}
html,body,[class*="css"]{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}
[data-testid="stMetric"]{border-top:1px solid rgba(128,128,128,.25);padding-top:.8rem}
[data-testid="stTabs"] button{font-weight:600}
</style>""",unsafe_allow_html=True)
st.title("Конструктор инструкций")
st.caption("Черновик по ТЗ → проверка качества → доработка → ручное согласование")
for key,value in {"draft":"","draft_editor":"","previous_draft":"","report":None,"generation_seconds":None,"last_generation_status":None}.items():st.session_state.setdefault(key,value)
if st.session_state.get("pending_draft") is not None:
    st.session_state.draft_editor=st.session_state.pop("pending_draft")

def current_source()->SourceInput:
    names=[("instruction_type","internal"),("audience",""),("location",""),("purpose",""),("scenario",""),("context","")]
    return SourceInput(**{name:st.session_state.get(name,default) for name,default in names},comments=st.session_state.get("revision_comments",""))

def current_kb()->KnowledgeBase:
    if st.session_state.get("instruction_type","internal")=="internal":return load_internal_kb()
    return kb_from_uploads({name:st.session_state.get(f"kb_{name}") for name in ("system_prompt","rules","template","example")})

def allowed_source(source:SourceInput,kb:KnowledgeBase,previous:str="")->str:
    return source.as_source_text(previous)+"\n\n"+kb.rules+"\n\n"+kb.template

def run_evaluation(previous:str|None=None)->None:
    source=current_source();kb=current_kb();draft=st.session_state.draft_editor
    st.session_state.draft=draft;st.session_state.report=evaluate(draft,allowed_source(source,kb,previous or ""),previous)


def show_report(report:QualityReport|None)->None:
    if report is None:st.info("Загрузите или сформируйте черновик и нажмите «Оценить черновик».");return
    labels={"need_info":"Нужны исходные данные","needs_revision":"Нужна доработка","ready_for_human_review":"Готово к ручному согласованию"}
    st.subheader(labels.get(report.recommendation,report.recommendation))
    cols=st.columns(4)
    cols[0].metric("Итоговый балл",f"{report.score:.1f} / 100")
    cols[1].metric("Критические замечания",len(report.critical_issues))
    cols[2].metric("Метки «Уточнить»",report.unresolved_count)
    cols[3].metric("Изменено строк","—" if report.change_ratio is None else f"{report.change_ratio:.1f}%")
    for metric in report.metrics:
        st.write(f"**{metric.label}** — {metric.score:.1f} / 100 · вес {metric.weight}%")
        st.progress(metric.score/100);st.caption(metric.details)
    for issue in report.issues:
        if issue.severity=="critical":st.error(issue.message)
        elif issue.severity=="info":st.info(issue.message)
        else:st.warning(issue.message)
    if not report.issues:st.success("Нарушений не найдено. Фактическое содержание проверяет человек.")

config=LLMConfig.from_env()
with st.sidebar:
    st.header("Настройки")
    if config.configured:st.success(f"LLM подключена: {config.model}");st.caption(config.base_url)
    else:st.warning("API не настроен");st.caption("Заполните .env по образцу .env.example. Проверка и экспорт доступны без API.")
    st.divider();st.caption("Порог: 85 баллов и ни одного критического замечания. Это не автоматическое согласование.")
    if st.button("Загрузить демонстрационный черновик",use_container_width=True):
        demo=(ROOT/"inputs"/"test_run_draft.md").read_text(encoding="utf-8")
        st.session_state.draft=demo;st.session_state.draft_editor=demo;st.session_state.previous_draft=""
        st.session_state.report=evaluate(demo,"");st.session_state.last_generation_status="NEED_INFO"

compose_tab,review_tab,metrics_tab=st.tabs(["1. Вводные и составление","2. Проверка и доработка","3. Метрики и согласование"])

with compose_tab:
    st.header("Параметры инструкции")
    st.radio("Тип",["internal","external"],format_func=lambda x:"Внутренняя" if x=="internal" else "Внешняя",key="instruction_type",horizontal=True)
    if st.session_state.instruction_type=="external":
        st.info("Загрузите собственные материалы. Встроенный внутренний пример не будет использован.")
        labels={"system_prompt":"Системный промпт","rules":"Правила","template":"Шаблон","example":"Пример"}
        for column,(name,label) in zip(st.columns(4),labels.items()):
            with column:st.file_uploader(label,type=["md","txt"],key=f"kb_{name}")
    else:st.caption("Подключены приложённые промпт, правила, шаблон и пример внутренней инструкции.")
    left,right=st.columns(2)
    with left:
        st.text_input("Для кого инструкция *",key="audience",placeholder="Роль или группа сотрудников")
        st.text_input("Где находится функция *",key="location",placeholder="Раздел, вкладка, кнопка или ссылка")
        st.text_area("Что функция делает и когда используется *",key="purpose",height=120)
    with right:
        st.text_area("Основной сценарий по шагам *",key="scenario",height=180,placeholder="1. Откройте …\n2. Нажмите …")
        st.text_area("ТЗ и контекст задачи *",key="context",height=180,placeholder="Исходное ТЗ, ограничения, названия полей и кнопок")
    try:
        kb=current_kb()
        if kb.missing():st.warning("Не загружены: "+", ".join(kb.missing()))
    except ValueError as error:st.error(str(error))
    generate=st.button("Сформировать инструкцию",type="primary",disabled=not config.configured)
    if not config.configured:st.caption("Генерация станет доступна после настройки API. Готовый текст можно вставить на следующей вкладке.")
    if generate:
        source=current_source();missing=source.missing_required()
        if missing:st.error("Заполните обязательные вводные: "+"; ".join(missing))
        else:
            try:
                started=time.perf_counter()
                with st.spinner("Формирую черновик по базе знаний…"):parsed=generate_instruction(source,current_kb(),config)
                st.session_state.generation_seconds=round(time.perf_counter()-started,1)
                st.session_state.last_generation_status=parsed.status;st.session_state.previous_draft=""
                raw=parsed.body if parsed.status=="READY" and parsed.valid else ("STATUS: "+parsed.status+"\n\n"+parsed.body).strip()
                st.session_state.draft=raw;st.session_state.draft_editor=raw
                st.session_state.report=evaluate(raw,allowed_source(source,current_kb()))
                for error in parsed.errors:st.error(error)
                if parsed.valid and parsed.status=="NEED_INFO":st.info("Модель запросила недостающие данные. Вопросы — на вкладке проверки.")
                elif parsed.valid:st.success("Черновик сформирован. Перейдите к автоматической проверке.")
            except (ValueError,LLMError) as error:st.error(str(error))

with review_tab:
    st.header("Черновик")
    if st.session_state.get("revision_notice"):st.success(st.session_state.pop("revision_notice"))
    if st.button("Оценить черновик",type="primary"):run_evaluation()
    view=st.radio("Режим",["Редактор","Предпросмотр","Сравнение до/после"],horizontal=True)
    if view=="Редактор":
        st.text_area("Markdown",key="draft_editor",height=520,label_visibility="collapsed")
        st.session_state.draft=st.session_state.draft_editor
    elif view=="Предпросмотр":
        if st.session_state.draft_editor:st.markdown(st.session_state.draft_editor)
        else:st.info("Вставьте черновик в редакторе или сформируйте его на первой вкладке.")
    elif st.session_state.previous_draft:
        diff="\n".join(difflib.unified_diff(st.session_state.previous_draft.splitlines(),st.session_state.draft_editor.splitlines(),fromfile="До",tofile="После",lineterm=""))
        st.code(diff or "Изменений нет",language="diff")
    else:st.info("Сравнение появится после первой доработки.")
    st.divider();show_report(st.session_state.report)
    st.subheader("Доработка по замечаниям")
    st.text_area("Замечания проверяющего",key="revision_comments",height=130,placeholder="Перечислите только необходимые правки")
    if st.button("Применить замечания через LLM",disabled=not config.configured):
        try:
            previous=st.session_state.draft_editor
            with st.spinner("Вношу только перечисленные правки…"):parsed=revise_instruction(current_source(),current_kb(),previous,st.session_state.revision_comments,config)
            if parsed.status=="READY" and parsed.valid:
                st.session_state.previous_draft=previous;st.session_state.draft=parsed.body;st.session_state.pending_draft=parsed.body
                st.session_state.report=evaluate(parsed.body,allowed_source(current_source(),current_kb(),previous),previous)
                st.session_state.revision_notice="Доработка получена и повторно оценена. Проверьте сравнение до/после."
                st.rerun()
            else:st.error("Модель не вернула корректный READY: "+"; ".join(parsed.errors))
        except (ValueError,LLMError) as error:st.error(str(error))

with metrics_tab:
    st.header("Результат и согласование")
    show_report(st.session_state.report)
    report=st.session_state.report
    if report:
        verdict=st.selectbox("Вердикт человека",["pending","needs_revision","approved"],format_func=lambda x:{"pending":"Не проверено","needs_revision":"Требует доработки","approved":"Согласовано"}[x])
        if st.button("Сохранить оценку в историю",type="primary"):
            record=append_metrics(HISTORY_PATH,report,st.session_state.instruction_type,verdict,st.session_state.generation_seconds)
            if verdict=="approved" and report.recommendation!="ready_for_human_review":st.warning("Ручное согласование сохранено, хотя автоматическая рекомендация отличается.")
            else:st.success(f"Оценка сохранена: {record['id'][:8]}")
        st.subheader("Выгрузка")
        if st.session_state.draft_editor.startswith("STATUS: NEED_INFO"):
            st.info("При NEED_INFO выгружаются только вопросы, инструкция ещё не сформирована.")
            st.download_button("Скачать вопросы (Markdown)",markdown_bytes(st.session_state.draft_editor),"questions.md","text/markdown")
        elif st.session_state.draft_editor.strip():
            name="instruction_approved" if verdict=="approved" else "instruction_draft"
            first,second=st.columns(2)
            first.download_button("Скачать Markdown",markdown_bytes(st.session_state.draft_editor),name+".md","text/markdown",use_container_width=True)
            second.download_button("Скачать DOCX",docx_bytes(st.session_state.draft_editor),name+".docx","application/vnd.openxmlformats-officedocument.wordprocessingml.document",use_container_width=True)
        else:st.info("Сначала добавьте черновик.")
    st.divider();st.subheader("Метрики пилота")
    summary=summarise(load_metrics(HISTORY_PATH));cols=st.columns(4)
    cols[0].metric("Сохранено оценок",summary["count"])
    cols[1].metric("Медиана балла","—" if summary["median_score"] is None else summary["median_score"])
    cols[2].metric("Доля готовых к согласованию","—" if summary["ready_rate"] is None else f"{summary['ready_rate']}%")
    cols[3].metric("Медиана изменённых строк","—" if summary["median_change"] is None else f"{summary['median_change']}%")
    st.caption("В истории только метрики и вердикты: текст ТЗ, инструкции и ключи не сохраняются.")
    st.write("**Целевые показатели для пилота**")
    st.markdown("""
- 100% прохождения критических проверок; 0 неподтверждённых ссылок и названий UI в согласованных документах.
- Не менее 70% черновиков без структурной переработки; медиана изменённых строк не более 20%.
- Не более 2 итераций; снижение медианного времени проверки минимум на 50% против ручного baseline.
- Точность и полнота NEED_INFO не ниже 90% на размеченном наборе; p95 генерации до 120 секунд.
""")
    st.caption("Это цели, а не достигнутые результаты. Для времени и NEED_INFO нужны baseline и размеченная выборка.")
