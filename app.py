from __future__ import annotations
import difflib
import io
import time
from pathlib import Path

import streamlit as st

from instruction_mvp.agents import load_agents
from instruction_mvp.exporters import docx_bytes, markdown_bytes
from instruction_mvp.history import append_metrics, load_metrics, summarise
from instruction_mvp.knowledge import MAX_FILE_BYTES, KnowledgeBase, load_internal_kb
from instruction_mvp.llm import LLMConfig, LLMError, generate_instruction, revise_instruction
from instruction_mvp.models import QualityReport, SourceInput
from instruction_mvp.quality import evaluate

ROOT = Path(__file__).resolve().parent
HISTORY_PATH = ROOT / "data" / "metrics.jsonl"

st.set_page_config(page_title="Конструктор инструкций", layout="wide")
st.markdown("""<style>
.block-container{max-width:1320px;padding-top:2rem;padding-bottom:4rem}
html,body,[class*="css"]{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}
[data-testid="stMetric"]{border-top:1px solid rgba(128,128,128,.25);padding-top:.8rem}
[data-testid="stTabs"] button{font-weight:600}
[data-testid="stFileUploader"] section{border:1.5px dashed rgba(128,128,128,.45);border-radius:12px;padding:1.2rem 1rem}
[data-testid="stFileUploader"] section:hover{border-color:#5eead4}
</style>""", unsafe_allow_html=True)

for key, value in {"draft": "", "draft_editor": "", "previous_draft": "", "report": None,
                   "generation_seconds": None, "last_generation_status": None, "tz": "",
                   "doc_type": "Внутренняя", "ext_rules": None, "ext_example": None}.items():
    st.session_state.setdefault(key, value)
if st.session_state.get("pending_draft") is not None:
    st.session_state.draft_editor = st.session_state.pop("pending_draft")


def read_text_file(file, label: str) -> str:
    if file is None:
        return ""
    data = file.getvalue()
    if len(data) > MAX_FILE_BYTES:
        raise ValueError(f"Файл {file.name} больше 2 МБ.")
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError as error:
        raise ValueError(f"Файл {file.name} ({label}) должен быть в UTF-8.") from error


def external_kb(rules_file, example_file) -> KnowledgeBase:
    """Внешняя ветка: общие системный промпт и шаблон + загруженные правила и пример внешних инструкций."""
    internal = load_internal_kb()
    return KnowledgeBase(
        system_prompt=internal.system_prompt,
        rules=read_text_file(rules_file, "правила"),
        template=internal.template,
        example=read_text_file(example_file, "пример"),
    )


def attachments_text(files) -> str:
    """Извлекает текст из приложенных файлов: txt/md напрямую, docx через python-docx, картинки — меткой."""
    parts = []
    for file in files or []:
        name = file.name.lower()
        data = file.getvalue()
        try:
            if name.endswith((".txt", ".md")):
                parts.append(f"## Файл: {file.name}\n" + data.decode("utf-8-sig", errors="replace"))
            elif name.endswith(".docx"):
                from docx import Document
                document = Document(io.BytesIO(data))
                parts.append(f"## Файл: {file.name}\n" + "\n".join(p.text for p in document.paragraphs if p.text.strip()))
            elif name.endswith((".png", ".jpg", ".jpeg")):
                parts.append(f"[Макет-изображение: {file.name}. Содержимое не читается — опирайся только на описание во вводных.]")
        except Exception as error:  # noqa: BLE001 — один битый файл не должен ломать остальные
            parts.append(f"[Файл {file.name} не прочитан: {error}]")
    return "\n\n".join(parts)


def current_source(extra: str = "") -> SourceInput:
    context = st.session_state.get("tz", "").strip()
    if extra.strip():
        context = (context + "\n\n# Приложенные материалы\n" + extra.strip()).strip()
    return SourceInput(instruction_type=st.session_state.get("instruction_type", "internal"), context=context,
                       comments=st.session_state.get("revision_comments", ""), free_form=True)


def current_kb() -> KnowledgeBase:
    if st.session_state.get("instruction_type", "internal") == "external":
        return external_kb(st.session_state.get("ext_rules"), st.session_state.get("ext_example"))
    return load_internal_kb()


def allowed_source(source: SourceInput, kb: KnowledgeBase, previous: str = "") -> str:
    return source.as_source_text(previous) + "\n\n" + kb.rules + "\n\n" + kb.template


def run_evaluation(previous: str | None = None) -> None:
    source = current_source(); kb = current_kb(); draft = st.session_state.draft_editor
    st.session_state.draft = draft; st.session_state.report = evaluate(draft, allowed_source(source, kb, previous or ""), previous)


def show_report(report: QualityReport | None) -> None:
    if report is None:
        st.info("Загрузите или сформируйте черновик и нажмите «Оценить черновик».")
        return
    labels = {"need_info": "Нужны исходные данные", "needs_revision": "Нужна доработка", "ready_for_human_review": "Готово к ручному согласованию"}
    st.subheader(labels.get(report.recommendation, report.recommendation))
    cols = st.columns(4)
    cols[0].metric("Итоговый балл", f"{report.score:.1f} / 100")
    cols[1].metric("Критические замечания", len(report.critical_issues))
    cols[2].metric("Метки «Уточнить»", report.unresolved_count)
    cols[3].metric("Изменено строк", "—" if report.change_ratio is None else f"{report.change_ratio:.1f}%")
    for metric in report.metrics:
        st.write(f"**{metric.label}** — {metric.score:.1f} / 100 · вес {metric.weight}%")
        st.progress(metric.score / 100); st.caption(metric.details)
    for issue in report.issues:
        if issue.severity == "critical":
            st.error(issue.message)
        elif issue.severity == "info":
            st.info(issue.message)
        else:
            st.warning(issue.message)
    if not report.issues:
        st.success("Нарушений не найдено. Фактическое содержание проверяет человек.")


agents = load_agents()
with st.sidebar:
    st.header("Настройки")
    agent_names = [agent.name for agent in agents]
    selected_name = st.selectbox("Агент", agent_names, help="Список агентов настраивается в файле agents.json (образец — agents.example.json).")
    agent = agents[agent_names.index(selected_name)]
    config = agent.config
    if agent.note:
        st.caption(agent.note)
    if config.configured:
        st.success(f"Агент подключён: {config.model}")
        st.caption(config.base_url)
    else:
        st.warning("API агента не настроен")
        st.caption("Заполните agents.json по образцу agents.example.json и задайте ключи в .env. Проверка и экспорт доступны без API.")
    st.divider(); st.caption("Порог: 85 баллов и ни одного критического замечания. Это не автоматическое согласование.")
    if st.button("Загрузить демонстрационный черновик", use_container_width=True):
        demo = (ROOT / "inputs" / "test_run_draft.md").read_text(encoding="utf-8")
        st.session_state.draft = demo; st.session_state.draft_editor = demo; st.session_state.previous_draft = ""
        st.session_state.report = evaluate(demo, ""); st.session_state.last_generation_status = "NEED_INFO"

compose_tab, review_tab, metrics_tab = st.tabs(["Вводные", "Проверка и доработка", "Метрики и согласование"])

with compose_tab:
    head_left, head_right = st.columns([3, 2])
    with head_left:
        st.markdown("### Вводные")
    with head_right:
        selected_type = st.segmented_control("Тип инструкции", ["Внутренняя", "Внешняя"],
                                             key="doc_type", label_visibility="collapsed")
        st.session_state.instruction_type = "external" if selected_type == "Внешняя" else "internal"
    if st.session_state.instruction_type == "external":
        st.caption("Внешняя ветка: системный промпт и шаблон общие, правила и пример — внешние.")
        ext_left, ext_right = st.columns(2)
        with ext_left:
            st.file_uploader("Правила внешних инструкций (.md, .txt) *", type=["md", "txt"],
                             key="ext_rules", label_visibility="visible")
        with ext_right:
            st.file_uploader("Пример внешней инструкции (.md, .txt) *", type=["md", "txt"],
                             key="ext_example", label_visibility="visible")
    st.markdown("**ТЗ и контекст задачи**")
    st.text_area("ТЗ и контекст задачи", key="tz", height=230, label_visibility="collapsed",
                 placeholder="Вставьте текст ТЗ: что делает функция, где находится, для кого, шаги, поля, ограничения. "
                             "Названия кнопок и полей берутся отсюда.")
    uploaded = st.file_uploader("Прикрепить файлы", type=["docx", "txt", "md", "png", "jpg", "jpeg"],
                                accept_multiple_files=True, label_visibility="collapsed")
    st.caption("Word (.docx), текст (.txt, .md) и картинки с макетами. Можно перетащить сюда. PDF не читается: сохраните как Word.")
    if uploaded:
        st.caption("Прикреплено файлов: " + str(len(uploaded)))
    try:
        kb_check = current_kb()
        if kb_check.missing():
            st.warning("Не загружены: " + ", ".join(kb_check.missing()))
    except ValueError as error:
        st.error(str(error))

    btn_generate, btn_clear = st.columns([1.6, 1])
    generate_clicked = btn_generate.button("Сформировать инструкцию", type="primary",
                                           disabled=not config.configured, use_container_width=True)
    if not config.configured:
        st.caption("Генерация станет доступна после настройки API. Готовый текст можно вставить на следующей вкладке.")
    if btn_clear.button("Очистить", use_container_width=True):
        st.session_state.tz = ""

    st.divider()
    st.markdown("### Результат")

    has_result = bool(st.session_state.draft_editor.strip()) and st.session_state.last_generation_status in ("READY", "NEED_INFO")
    if not has_result:
        card_left, card_right = st.columns(2)
        with card_left:
            with st.container(border=True):
                st.markdown("**Данных не хватает**")
                st.caption("Вы получите вопросы к автору ТЗ.")
                st.code("1.  Для кого инструкция?\n2.  Где находится страница?", language=None)
        with card_right:
            with st.container(border=True):
                st.markdown("**Данных хватает**")
                st.caption("Вы получите черновик. Места под картинки помечены.")
                st.code('4.  Нажмите «Сформировать отчет».\n[СКРИНШОТ: форма с полями «От» и «До»]', language=None)
    elif st.session_state.last_generation_status == "NEED_INFO":
        with st.container(border=True):
            st.markdown("**Данных не хватает**")
            st.caption("Агент вернул вопросы к автору ТЗ. Инструкция не сформирована.")
            st.markdown(st.session_state.draft_editor.removeprefix("STATUS: NEED_INFO").strip())
    else:
        with st.container(border=True):
            st.markdown("**Данных хватает**")
            st.caption("Черновик сформирован. Места под картинки помечены. Полная проверка — на следующей вкладке.")
            st.markdown(st.session_state.draft_editor)
            first, second = st.columns(2)
            first.download_button("Скачать Markdown", markdown_bytes(st.session_state.draft_editor),
                                  "instruction_draft.md", "text/markdown", use_container_width=True)
            second.download_button("Скачать DOCX", docx_bytes(st.session_state.draft_editor),
                                   "instruction_draft.docx",
                                   "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                                   use_container_width=True)

    if generate_clicked:
        if not st.session_state.get("tz", "").strip():
            st.error("Вставьте текст ТЗ.")
        else:
            try:
                started = time.perf_counter()
                source = current_source(attachments_text(uploaded))
                kb = current_kb()
                with st.spinner("Формирую черновик по базе знаний…"):
                    parsed = generate_instruction(source, kb, config)
                st.session_state.generation_seconds = round(time.perf_counter() - started, 1)
                st.session_state.last_generation_status = parsed.status; st.session_state.previous_draft = ""
                raw = parsed.body if parsed.status == "READY" and parsed.valid else ("STATUS: " + parsed.status + "\n\n" + parsed.body).strip()
                st.session_state.draft = raw; st.session_state.draft_editor = raw
                st.session_state.report = evaluate(raw, allowed_source(source, kb))
                for error in parsed.errors:
                    st.error(error)
                if parsed.valid and parsed.status == "NEED_INFO":
                    st.info("Агент запросил недостающие данные. Вопросы — в блоке результата.")
                elif parsed.valid:
                    st.success("Черновик сформирован. Скачайте файл ниже или перейдите к автоматической проверке.")
            except (ValueError, LLMError) as error:
                st.error(str(error))

with review_tab:
    st.header("Черновик")
    if st.session_state.get("revision_notice"):
        st.success(st.session_state.pop("revision_notice"))
    if st.button("Оценить черновик", type="primary"):
        run_evaluation()
    view = st.radio("Режим", ["Редактор", "Предпросмотр", "Сравнение до/после"], horizontal=True)
    if view == "Редактор":
        st.text_area("Markdown", key="draft_editor", height=520, label_visibility="collapsed")
        st.session_state.draft = st.session_state.draft_editor
    elif view == "Предпросмотр":
        if st.session_state.draft_editor:
            st.markdown(st.session_state.draft_editor)
        else:
            st.info("Вставьте черновик в редакторе или сформируйте его на первой вкладке.")
    elif st.session_state.previous_draft:
        diff = "\n".join(difflib.unified_diff(st.session_state.previous_draft.splitlines(),
                                              st.session_state.draft_editor.splitlines(),
                                              fromfile="До", tofile="После", lineterm=""))
        st.code(diff or "Изменений нет", language="diff")
    else:
        st.info("Сравнение появится после первой доработки.")
    st.divider(); show_report(st.session_state.report)
    st.subheader("Доработка по замечаниям")
    st.text_area("Замечания проверяющего", key="revision_comments", height=130,
                 placeholder="Перечислите только необходимые правки")
    if st.button("Применить замечания через LLM", disabled=not config.configured):
        try:
            previous = st.session_state.draft_editor
            with st.spinner("Вношу только перечисленные правки…"):
                parsed = revise_instruction(current_source(), current_kb(), previous,
                                            st.session_state.revision_comments, config)
            if parsed.status == "READY" and parsed.valid:
                st.session_state.previous_draft = previous; st.session_state.draft = parsed.body
                st.session_state.pending_draft = parsed.body
                st.session_state.report = evaluate(parsed.body, allowed_source(current_source(), current_kb(), previous), previous)
                st.session_state.revision_notice = "Доработка получена и повторно оценена. Проверьте сравнение до/после."
                st.rerun()
            else:
                st.error("Модель не вернула корректный READY: " + "; ".join(parsed.errors))
        except (ValueError, LLMError) as error:
            st.error(str(error))

with metrics_tab:
    st.header("Результат и согласование")
    show_report(st.session_state.report)
    report = st.session_state.report
    if report:
        verdict = st.selectbox("Вердикт человека", ["pending", "needs_revision", "approved"],
                               format_func=lambda x: {"pending": "Не проверено", "needs_revision": "Требует доработки", "approved": "Согласовано"}[x])
        if st.button("Сохранить оценку в историю", type="primary"):
            record = append_metrics(HISTORY_PATH, report, st.session_state.instruction_type, verdict,
                                    st.session_state.generation_seconds)
            if verdict == "approved" and report.recommendation != "ready_for_human_review":
                st.warning("Ручное согласование сохранено, хотя автоматическая рекомендация отличается.")
            else:
                st.success(f"Оценка сохранена: {record['id'][:8]}")
        st.subheader("Выгрузка")
        if st.session_state.draft_editor.startswith("STATUS: NEED_INFO"):
            st.info("При NEED_INFO выгружаются только вопросы, инструкция ещё не сформирована.")
            st.download_button("Скачать вопросы (Markdown)", markdown_bytes(st.session_state.draft_editor),
                               "questions.md", "text/markdown")
        elif st.session_state.draft_editor.strip():
            name = "instruction_approved" if verdict == "approved" else "instruction_draft"
            first, second = st.columns(2)
            first.download_button("Скачать Markdown", markdown_bytes(st.session_state.draft_editor),
                                  name + ".md", "text/markdown", use_container_width=True)
            second.download_button("Скачать DOCX", docx_bytes(st.session_state.draft_editor), name + ".docx",
                                   "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                                   use_container_width=True)
        else:
            st.info("Сначала добавьте черновик.")
    st.divider(); st.subheader("Метрики пилота")
    summary = summarise(load_metrics(HISTORY_PATH)); cols = st.columns(4)
    cols[0].metric("Сохранено оценок", summary["count"])
    cols[1].metric("Медиана балла", "—" if summary["median_score"] is None else summary["median_score"])
    cols[2].metric("Доля готовых к согласованию", "—" if summary["ready_rate"] is None else f"{summary['ready_rate']}%")
    cols[3].metric("Медиана изменённых строк", "—" if summary["median_change"] is None else f"{summary['median_change']}%")
    st.caption("В истории только метрики и вердикты: текст ТЗ, инструкции и ключи не сохраняются.")
    st.write("**Целевые показатели для пилота**")
    st.markdown("""
- 100% прохождения критических проверок; 0 неподтверждённых ссылок и названий UI в согласованных документах.
- Не менее 70% черновиков без структурной переработки; медиана изменённых строк не более 20%.
- Не более 2 итераций; снижение медианного времени проверки минимум на 50% против ручного baseline.
- Точность и полнота NEED_INFO не ниже 90% на размеченном наборе; p95 генерации до 120 секунд.
""")
    st.caption("Это цели, а не достигнутые результаты. Для времени и NEED_INFO нужны baseline и размеченная выборка.")
