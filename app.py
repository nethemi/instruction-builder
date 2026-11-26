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
from instruction_mvp.llm import LLMError, generate_instruction, revise_instruction
from instruction_mvp.models import QualityReport, SourceInput
from instruction_mvp.quality import evaluate

ROOT = Path(__file__).resolve().parent
HISTORY_PATH = ROOT / "data" / "metrics.jsonl"
MAX_ATTACHMENT_BYTES = 25 * 1024 * 1024

st.set_page_config(page_title="Конструктор инструкций", layout="wide")
st.markdown("""<style>
.block-container{max-width:1320px;padding-top:2rem;padding-bottom:4rem}
html,body,[class*="css"]{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}
[data-testid="stFileUploader"] section{border:1.5px dashed rgba(128,128,128,.45);border-radius:12px;padding:1.1rem 1rem}
[data-testid="stFileUploader"] section:hover{border-color:#5eead4}

.stepper{display:flex;align-items:center;gap:14px;margin:0 auto 1.6rem auto;max-width:760px}
.step{display:flex;align-items:center;gap:10px;white-space:nowrap}
.step .circle{width:30px;height:30px;border-radius:50%;display:inline-flex;align-items:center;justify-content:center;
  font-weight:700;border:1.5px dashed rgba(128,128,128,.6);color:rgba(200,200,200,.75)}
.step.active .circle{background:#5eead4;border:1.5px solid #5eead4;color:#0b2530}
.step.done .circle{border-style:solid;border-color:#5eead4;color:#5eead4}
.step .label{color:rgba(200,200,200,.75);font-size:15px}
.step.active .label{color:#fff;font-weight:600}
.step.done .label{color:#5eead4}
.stepper .line{flex:1;height:1.5px;background:rgba(128,128,128,.35);min-width:30px}
</style>""", unsafe_allow_html=True)

DEFAULTS = {"step": 1, "draft": "", "draft_editor": "", "previous_draft": "", "report": None,
            "generation_seconds": None, "last_generation_status": None, "tz": "", "extra_data": "",
            "audience": "", "doc_type": "Внутренняя", "ext_rules": None, "ext_example": None,
            "revision_comments": ""}
for key, value in DEFAULTS.items():
    st.session_state.setdefault(key, value)
if st.session_state.get("pending_draft") is not None:
    st.session_state.draft_editor = st.session_state.pop("pending_draft")
    st.session_state.draft = st.session_state.draft_editor


def stepper_html(current: int) -> str:
    steps = [(1, "Формирование"), (2, "Редактирование"), (3, "Оценка")]
    parts = ['<div class="stepper">']
    for index, (number, label) in enumerate(steps):
        state = "active" if number == current else ("done" if number < current else "")
        parts.append(f'<div class="step {state}"><span class="circle">{number}</span><span class="label">{label}</span></div>')
        if index < len(steps) - 1:
            parts.append('<div class="line"></div>')
    parts.append("</div>")
    return "".join(parts)


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
    """Извлекает текст из приложенных файлов: txt/md напрямую, docx через python-docx, pdf через pypdf, картинки — меткой."""
    parts = []
    for file in files or []:
        name = file.name.lower()
        data = file.getvalue()
        if len(data) > MAX_ATTACHMENT_BYTES:
            parts.append(f"[Файл {file.name} больше 25 МБ — пропущен.]")
            continue
        try:
            if name.endswith((".txt", ".md")):
                parts.append(f"## Файл: {file.name}\n" + data.decode("utf-8-sig", errors="replace"))
            elif name.endswith(".docx"):
                from docx import Document
                document = Document(io.BytesIO(data))
                parts.append(f"## Файл: {file.name}\n" + "\n".join(p.text for p in document.paragraphs if p.text.strip()))
            elif name.endswith(".pdf"):
                from pypdf import PdfReader
                reader = PdfReader(io.BytesIO(data))
                pages = [(page.extract_text() or "").strip() for page in reader.pages]
                parts.append(f"## Файл: {file.name}\n" + "\n\n".join(p for p in pages if p))
            elif name.endswith((".png", ".jpg", ".jpeg")):
                parts.append(f"[Макет-изображение: {file.name}. Содержимое не читается — опирайся только на описание во вводных.]")
        except Exception as error:  # noqa: BLE001 — один битый файл не должен ломать остальные
            parts.append(f"[Файл {file.name} не прочитан: {error}]")
    return "\n\n".join(parts)


def current_source(extra: str = "") -> SourceInput:
    context = st.session_state.get("tz", "").strip()
    if extra.strip():
        context = (context + "\n\n# Приложенные материалы\n" + extra.strip()).strip()
    return SourceInput(
        instruction_type="external" if st.session_state.get("doc_type") == "Внешняя" else "internal",
        audience=st.session_state.get("audience", "").strip(),
        context=context,
        comments=st.session_state.get("revision_comments", ""),
        free_form=True,
    )


def current_kb() -> KnowledgeBase:
    if st.session_state.get("doc_type") == "Внешняя":
        return external_kb(st.session_state.get("ext_rules"), st.session_state.get("ext_example"))
    return load_internal_kb()


def allowed_source(source: SourceInput, kb: KnowledgeBase, previous: str = "") -> str:
    return source.as_source_text(previous) + "\n\n" + kb.rules + "\n\n" + kb.template


def run_evaluation(previous: str | None = None) -> None:
    source = current_source(); kb = current_kb(); draft = st.session_state.draft_editor
    st.session_state.draft = draft; st.session_state.report = evaluate(draft, allowed_source(source, kb, previous or ""), previous)


def show_report(report: QualityReport | None) -> None:
    if report is None:
        st.info("Сначала оцените черновик на шаге «Оценка».")
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
agent_names = [agent.name for agent in agents]
with st.sidebar:
    st.header("Настройки")
    st.caption("Порог: 85 баллов и ни одного критического замечания. Это не автоматическое согласование.")
    if st.button("Загрузить демонстрационный черновик", use_container_width=True):
        demo = (ROOT / "inputs" / "test_run_draft.md").read_text(encoding="utf-8")
        st.session_state.draft = demo; st.session_state.draft_editor = demo; st.session_state.previous_draft = ""
        st.session_state.report = evaluate(demo, ""); st.session_state.last_generation_status = "NEED_INFO"; st.session_state.step = 2
        st.rerun()

st.markdown(stepper_html(st.session_state.step), unsafe_allow_html=True)

# ---------- Шаг 1. Формирование ----------
if st.session_state.step == 1:
    _, center, _ = st.columns([1, 2.2, 1])
    with center:
        selected_name = st.selectbox("Агент", agent_names,
                                     help="Список агентов настраивается в файле agents.json (образец — agents.example.json).")
        agent = agents[agent_names.index(selected_name)]
        config = agent.config
        if agent.note:
            st.caption(agent.note)
        if not config.configured:
            st.warning("API агента не настроен: задайте ключи в .env. Проверка и экспорт доступны без API.")

        st.radio("Тип инструкции", ["Внутренняя", "Внешняя"], key="doc_type", horizontal=True,
                 label_visibility="visible")
        if st.session_state.doc_type == "Внешняя":
            st.caption("Внешняя ветка: системный промпт и шаблон общие, правила и пример — внешние.")
            ext_left, ext_right = st.columns(2)
            with ext_left:
                st.file_uploader("Правила внешних инструкций (.md, .txt) *", type=["md", "txt"], key="ext_rules")
            with ext_right:
                st.file_uploader("Пример внешней инструкции (.md, .txt) *", type=["md", "txt"], key="ext_example")

        st.text_input("Для кого инструкция", key="audience", placeholder="Роль или группа сотрудников / клиентов")
        st.text_area("Опишите задачу", key="tz", height=200,
                     placeholder="Контекст и ТЗ: что делает функция, где находится, шаги, поля, ограничения.")
        uploaded = st.file_uploader("Загрузить файлы", type=["pdf", "png", "jpg", "jpeg", "txt", "docx", "md"],
                                    accept_multiple_files=True, label_visibility="collapsed")
        st.caption("PDF, PNG, JPG, TXT, DOCX, MD. До 25 МБ на файл.")
        try:
            kb_check = current_kb()
            if kb_check.missing():
                st.warning("Не загружены: " + ", ".join(kb_check.missing()))
        except ValueError as error:
            st.error(str(error))

        need_info = st.session_state.last_generation_status == "NEED_INFO" and bool(st.session_state.draft_editor.strip())
        if need_info:
            st.markdown("**Данных не хватает**")
            st.markdown(st.session_state.draft_editor.removeprefix("STATUS: NEED_INFO").strip())
            st.markdown("**Добавьте данные**")
            st.text_area("Ваши данные", key="extra_data", height=130, placeholder="Ваши данные…",
                         label_visibility="collapsed")
            extra_uploaded = st.file_uploader("Загрузить файлы", type=["pdf", "png", "txt"],
                                              accept_multiple_files=True, key="extra_upload",
                                              label_visibility="collapsed")
        else:
            extra_uploaded = None

        btn_main, btn_clear = st.columns([1.6, 1])

        def do_generate(extra_text: str = "") -> None:
            try:
                started = time.perf_counter()
                source = current_source(extra_text)
                kb = current_kb()
                with st.spinner("Формирую черновик…"):
                    parsed = generate_instruction(source, kb, config)
                st.session_state.generation_seconds = round(time.perf_counter() - started, 1)
                st.session_state.last_generation_status = parsed.status
                st.session_state.previous_draft = ""
                raw = parsed.body if parsed.status == "READY" and parsed.valid else ("STATUS: " + parsed.status + "\n\n" + parsed.body).strip()
                st.session_state.draft = raw; st.session_state.draft_editor = raw
                st.session_state.report = None
                for error in parsed.errors:
                    st.error(error)
                if parsed.valid and parsed.status == "NEED_INFO":
                    st.session_state.extra_data = ""
                    st.rerun()
                elif parsed.valid:
                    st.session_state.tz = source.context
                    st.session_state.step = 2
                    st.rerun()
            except (ValueError, LLMError) as error:
                st.error(str(error))

        if need_info:
            if btn_main.button("Продолжить формировать", type="primary", use_container_width=True):
                addition = st.session_state.get("extra_data", "").strip()
                files_text = attachments_text(extra_uploaded)
                if files_text:
                    addition = (addition + "\n\n" + files_text).strip()
                if not addition:
                    st.error("Добавьте данные или файл.")
                else:
                    st.session_state.tz = st.session_state.tz.rstrip() + "\n\n# Дополнительные данные\n" + addition
                    st.session_state.extra_data = ""
                    st.session_state.last_generation_status = None
                    do_generate()
        else:
            if btn_main.button("Сформировать", type="primary", disabled=not config.configured, use_container_width=True):
                if not st.session_state.get("tz", "").strip():
                    st.error("Опишите задачу.")
                else:
                    do_generate(attachments_text(uploaded))
        if btn_clear.button("Очистить", use_container_width=True):
            st.session_state.tz = ""; st.session_state.audience = ""; st.session_state.extra_data = ""
            st.session_state.last_generation_status = None; st.session_state.draft_editor = ""
            st.rerun()

# ---------- Шаг 2. Редактирование ----------
elif st.session_state.step == 2:
    _, center, _ = st.columns([1, 2.6, 1])
    with center:
        st.markdown("**Черновик**")
        view = st.radio("Режим", ["Редактор", "Предпросмотр", "Сравнение до/после"], horizontal=True,
                        label_visibility="collapsed")
        if view == "Редактор":
            st.text_area("Markdown", key="draft_editor", height=480, label_visibility="collapsed")
            st.session_state.draft = st.session_state.draft_editor
        elif view == "Предпросмотр":
            if st.session_state.draft_editor:
                st.markdown(st.session_state.draft_editor)
            else:
                st.info("Черновик пуст. Вернитесь на шаг «Формирование».")
        elif st.session_state.previous_draft:
            diff = "\n".join(difflib.unified_diff(st.session_state.previous_draft.splitlines(),
                                                  st.session_state.draft_editor.splitlines(),
                                                  fromfile="До", tofile="После", lineterm=""))
            st.code(diff or "Изменений нет", language="diff")
        else:
            st.info("Сравнение появится после первой доработки.")
        back, to_eval = st.columns([1, 1.6])
        if back.button("Назад", use_container_width=True):
            st.session_state.step = 1; st.rerun()
        if to_eval.button("Оценить", type="primary", use_container_width=True):
            if not st.session_state.draft_editor.strip():
                st.error("Черновик пуст.")
            else:
                run_evaluation(); st.session_state.step = 3; st.rerun()

# ---------- Шаг 3. Оценка ----------
else:
    _, center, _ = st.columns([1, 2.6, 1])
    with center:
        if st.session_state.get("revision_notice"):
            st.success(st.session_state.pop("revision_notice"))
        show_report(st.session_state.report)
        report = st.session_state.report

        st.divider()
        st.subheader("Доработка по замечаниям")
        selected_name_eval = st.selectbox("Агент", agent_names, key="eval_agent",
                                          label_visibility="visible")
        eval_agent = agents[agent_names.index(selected_name_eval)]
        st.text_area("Замечания проверяющего", key="revision_comments", height=120,
                     placeholder="Перечислите только необходимые правки")
        if st.button("Применить замечания через LLM", disabled=not eval_agent.config.configured,
                     use_container_width=True):
            try:
                previous = st.session_state.draft_editor
                with st.spinner("Вношу только перечисленные правки…"):
                    parsed = revise_instruction(current_source(), current_kb(), previous,
                                                st.session_state.revision_comments, eval_agent.config)
                if parsed.status == "READY" and parsed.valid:
                    st.session_state.previous_draft = previous; st.session_state.draft = parsed.body
                    st.session_state.pending_draft = parsed.body
                    st.session_state.report = evaluate(parsed.body, allowed_source(current_source(), current_kb(), previous), previous)
                    st.session_state.revision_notice = "Доработка получена и повторно оценена."
                    st.session_state.step = 2
                    st.rerun()
                else:
                    st.error("Модель не вернула корректный READY: " + "; ".join(parsed.errors))
            except (ValueError, LLMError) as error:
                st.error(str(error))

        if report:
            st.divider()
            st.subheader("Выгрузка")
            verdict = st.selectbox("Вердикт человека", ["pending", "needs_revision", "approved"],
                                   format_func=lambda x: {"pending": "Не проверено", "needs_revision": "Требует доработки", "approved": "Согласовано"}[x])
            save_col, _ = st.columns([1.6, 1])
            if save_col.button("Сохранить оценку в историю", type="primary", use_container_width=True):
                record = append_metrics(HISTORY_PATH, report,
                                        "external" if st.session_state.doc_type == "Внешняя" else "internal",
                                        verdict, st.session_state.generation_seconds)
                if verdict == "approved" and report.recommendation != "ready_for_human_review":
                    st.warning("Ручное согласование сохранено, хотя автоматическая рекомендация отличается.")
                else:
                    st.success(f"Оценка сохранена: {record['id'][:8]}")
            if st.session_state.draft_editor.startswith("STATUS: NEED_INFO"):
                st.download_button("Скачать вопросы (Markdown)", markdown_bytes(st.session_state.draft_editor),
                                   "questions.md", "text/markdown", key="step3_questions")
            elif st.session_state.draft_editor.strip():
                name = "instruction_approved" if verdict == "approved" else "instruction_draft"
                first, second = st.columns(2)
                first.download_button("Скачать Markdown", markdown_bytes(st.session_state.draft_editor),
                                      name + ".md", "text/markdown", use_container_width=True, key="step3_md")
                second.download_button("Скачать DOCX", docx_bytes(st.session_state.draft_editor), name + ".docx",
                                       "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                                       use_container_width=True, key="step3_docx")

        with st.expander("Метрики пилота"):
            summary = summarise(load_metrics(HISTORY_PATH)); cols = st.columns(4)
            cols[0].metric("Сохранено оценок", summary["count"])
            cols[1].metric("Медиана балла", "—" if summary["median_score"] is None else summary["median_score"])
            cols[2].metric("Доля готовых к согласованию", "—" if summary["ready_rate"] is None else f"{summary['ready_rate']}%")
            cols[3].metric("Медиана изменённых строк", "—" if summary["median_change"] is None else f"{summary['median_change']}%")
            st.caption("В истории только метрики и вердикты: текст ТЗ, инструкции и ключи не сохраняются.")
            st.markdown("""
- 100% прохождения критических проверок; 0 неподтверждённых ссылок и названий UI в согласованных документах.
- Не менее 70% черновиков без структурной переработки; медиана изменённых строк не более 20%.
- Не более 2 итераций; снижение медианного времени проверки минимум на 50% против ручного baseline.
- Точность и полнота NEED_INFO не ниже 90% на размеченном наборе; p95 генерации до 120 секунд.
""")
            st.caption("Это цели, а не достигнутые результаты.")

        back, _ = st.columns([1, 2.6])
        if back.button("Назад", key="step3_back", use_container_width=True):
            st.session_state.step = 2; st.rerun()
