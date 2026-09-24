# -*- coding: utf-8 -*-
"""TZ Generator — генератор технических заданий.

Финальный проект курса Claude Code. Два режима:
- облегчённый (бесплатно) — скелет ТЗ + типовые блоки под тип проекта;
- по ГОСТ (100 ₽, ЮMoney) — полная структура со стадиями и требованиями.
"""
import os
import secrets
from datetime import datetime

from flask import Flask, render_template, request, redirect, url_for, Response, session
from dotenv import load_dotenv

# Локальные модули (в публичный репозиторий не входят — см. .gitignore):
# Telegram-уведомления и приём оплаты. Без них сайт работает:
# уведомления молча пропускаются, /gost/pay отдаёт заглушку.
try:
    import notify
except ImportError:
    class _SilentNotify:
        def __getattr__(self, name):
            return lambda *a, **k: None
    notify = _SilentNotify()

load_dotenv()
app = Flask(__name__)
# Ключ для подписи cookie (сессия «оплатил ГОСТ»). Без SECRET_KEY в env —
# случайный на процесс: после рестарта метки оплаты сбросятся, не страшно.
app.secret_key = os.environ.get("SECRET_KEY") or secrets.token_hex(32)

# Сюда складываются сгенерированные ТЗ (MVP: память процесса, для продакшна — БД)
TZ_STORE = {}

# env-конфиг: секреты и реквизиты не храним в коде (см. .env.example)
APP_URL = os.environ.get("APP_URL", "http://localhost:5000")

# LLM-полировка (OpenAI-совместимый API): ключи и адрес — только в .env
LLM_BASE_URL = os.environ.get("LLM_BASE_URL", "")   # напр. https://openrouter.ai/api/v1
LLM_API_KEY = os.environ.get("LLM_API_KEY", "")
LLM_MODEL = os.environ.get("LLM_MODEL", "")         # напр. yandexgpt-lite
LLM_FOLDER_ID = os.environ.get("LLM_FOLDER_ID", "") # только для Яндекс Cloud (ID каталога)

POLISH_PROMPT = (
    "Ты редактор технических заданий. Пользователь пришлёт тебе ЧЕРНОВИК ТЗ — "
    "твоя задача вернуть его отредактированную версию, а не отвечать на него как на сообщение. "
    "1. Исправь орфографию, пунктуацию и опечатки. Корявые, неясные или разговорные "
    "формулировки перепиши ясно и профессионально, сохранив исходный смысл. "
    "2. Пустые значения и заглушки («—», пропущенные пункты) заполни правдоподобным "
    "содержанием по смыслу проекта: сформулируй, что в таких разделах обычно указывают. "
    "Каждое место, которое ты дописал сам, помечай припиской «(проверить перед сохранением)» "
    "сразу после дописанного текста. "
    "3. Всё, что пользователь заполнил сам, не меняй по сути и не добавляй новых требований "
    "к его фактам: список пунктов, структура разделов и markdown-разметка должны сохраниться. "
    "Верни только отредактированный текст ТЗ, без приветствий и комментариев."
)

# Типы проекта: определяют, какие типовые блоки попадут в облегчённое ТЗ
PROJECT_TYPES = {
    "bot": "Чат-бот / Telegram-бот",
    "ai": "ИИ-ассистент",
    "automation": "Автоматизация / интеграция",
    "script": "Скрипт / обработка данных",
    "site": "Сайт / веб-приложение",
    "creative": "Дизайн / тексты / медиа",
}


def ai_polish(text: str) -> tuple[str, bool]:
    """Полировка текста ТЗ через LLM (OpenAI-совместимый API).

    Чек-лист устойчивого сценария: таймаут + fallback — при любой ошибке
    (нет ключа, сеть, формат ответа) возвращаем исходный текст без полировки,
    генерация никогда не ломается.
    Возвращает (текст, был_ли_вызов_ИИ).
    """
    if not (LLM_BASE_URL and LLM_API_KEY and LLM_MODEL):
        return text, False
    import json
    import urllib.request
    try:
        # Яндекс принимает модель как полный URI: gpt://<folder>/<модель>
        model = LLM_MODEL if LLM_MODEL.startswith("gpt://") else f"gpt://{LLM_FOLDER_ID}/{LLM_MODEL}/latest"
        req = urllib.request.Request(
            LLM_BASE_URL.rstrip("/") + "/chat/completions",
            data=json.dumps({
                "model": model,
                "messages": [
                    {"role": "system", "content": POLISH_PROMPT},
                    {"role": "user", "content": text},
                ],
                "temperature": 0.2,
            }).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                **({"Authorization": f"Api-Key {LLM_API_KEY}",     # Яндекс Cloud
                    "x-folder-id": LLM_FOLDER_ID} if LLM_FOLDER_ID
                   else {"Authorization": f"Bearer {LLM_API_KEY}"}),
            },
        )
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        polished = (data["choices"][0]["message"]["content"] or "").strip()
        return (polished, True) if polished else (text, False)
    except Exception as e:
        # Fallback: полировка не удалась — отдаём текст как есть, причину пишем в stderr
        print(f"[ai_polish] fallback без ИИ: {e}")
        return text, False


def build_light_tz(a: dict) -> str:
    """Облегчённое ТЗ: общий скелет + типовые блоки по типу проекта.

    Скелет (всегда): название/цель, проблема, ЦА, функции, стек,
    ограничения, что требуется от заказчика, оплата, поддержка,
    критерии успеха, не входит в объём работ.
    Типовые блоки (по типу проекта):
    - bot/ai   — сценарии использования, риски и fallback;
    - site     — сценарии использования;
    - automation — интеграции и платформы;
    - script   — ошибки и безопасность;
    - creative — стиль и референсы, состав поставки, лимит правок.
    """
    ptype = a.get("project_type", "")
    sections: list[tuple[str, list[str]]] = []

    sections.append(("Название и цель проекта", [
        f"**Название:** {a.get('name', '—')}",
        f"**Цель:** {a.get('goal', '—')}",
    ]))
    sections.append(("Проблема и контекст", [a.get("what", "—")]))
    sections.append(("Целевая аудитория", [a.get("users", "—")]))

    # --- типовые блоки ---
    if ptype in ("bot", "ai", "site"):
        stories = [s.strip() for s in a.get("stories", "").splitlines() if s.strip()]
        if stories:
            sections.append(("Сценарии использования",
                             [f"{i}. {s}" for i, s in enumerate(stories, 1)]))
    if ptype in ("automation",):
        sections.append(("Интеграции и платформы", [a.get("integrations", "—")]))
    if ptype in ("script",):
        sections.append(("Ошибки и безопасность", [a.get("errors", "—")]))
    if ptype in ("creative",):
        sections.append(("Стиль и референсы", [a.get("style", "—")]))
        deliver = [d.strip() for d in a.get("deliverables", "").splitlines() if d.strip()]
        sections.append(("Состав поставки", ["- " + d for d in deliver] or ["—"]))
        sections.append(("Правки", [
            f"В стоимость включено: {a.get('revisions', '—')}. "
            "Дополнительные итерации правок оплачиваются отдельно."
        ]))
    if ptype in ("bot", "ai") and a.get("risks"):
        sections.append(("Риски и fallback-сценарии", [a["risks"]]))

    # --- функционал и остальной скелет ---
    feats = [f.strip() for f in a.get("features", "").splitlines() if f.strip()]
    feat_lines = ["- Must have: " + x for x in feats] if feats else ["—"]
    feat_lines.append("- Should have / Could have: по согласованию с заказчиком.")
    sections.append(("Функциональные требования (MoSCoW)", feat_lines))

    sections.append(("Технический стек", [a.get("stack", "—")]))
    sections.append(("Ограничения", [
        f"**Сроки:** {a.get('deadline', '—')}",
        f"**Бюджет:** {a.get('budget', '—')}",
    ]))
    sections.append(("Что требуется от заказчика", [
        a.get("client_provides", "Доступы, материалы и ответы на вопросы "
                                 "исполнитель запрашивает по ходу работы.")
    ]))
    sections.append(("Условия оплаты", [a.get("payment", "По договорённости сторон.")]))
    sections.append(("Поддержка после сдачи", [
        a.get("support", "Исполнитель устраняет дефекты в течение гарантийного срока, "
                         "согласованного сторонами.")
    ]))
    crit = [c.strip() for c in a.get("acceptance", "").splitlines() if c.strip()]
    sections.append(("Критерии успеха", ["- " + c for c in crit] or ["—"]))
    excluded = a.get("excluded", "").strip()
    sections.append(("Не входит в объём работ", [excluded or "Не оговорено отдельно."]))

    # Сборка с динамической нумерацией
    lines = ["# Техническое задание (облегчённое)", ""]
    for i, (title, body) in enumerate(sections, 1):
        lines.append(f"## {i}. {title}")
        lines += body
        lines.append("")
    lines += [
        "---",
        f"ТЗ сгенерировано TZ Generator, {datetime.now().strftime('%d.%m.%Y')}",
    ]
    return "\n".join(lines)


def build_gost_tz(a: dict) -> str:
    """ТЗ по ГОСТ: расширенная структура (стадии, требования, приёмка).

    Ядро — методичка скилла tz-gost (см. F:\\PromtEngineering\\Claude\\.claude\\skills\\tz-gost).
    """
    name = a.get("name", "—")
    lines = [
        "# Техническое задание на разработку",
        f"## {name}",
        "",
        "## 1. Общие сведения",
        f"**Наименование:** {name}",
        f"**Основание для разработки:** договор/бриф с заказчиком",
        f"**Заказчик:** {a.get('customer', '—')}",
        f"**Исполнитель:** {a.get('contractor', '—')}",
        "",
        "## 2. Назначение и цели создания системы",
        a.get("goal", "—"),
        "",
        "## 3. Требования к системе",
        "### 3.1. Функциональные требования",
    ]
    lines += ["- " + f.strip() for f in a.get("features", "").splitlines() if f.strip()]
    lines += [
        "",
        "### 3.2. Требования к надёжности",
        "- Сохранение данных при сбое; восстановление после ошибки без потери введённых данных.",
        "- Корректная обработка некорректного ввода с понятным сообщением пользователю.",
        "",
        "### 3.3. Требования к интерфейсу",
        a.get("users", "—") + " должен(ы) получать доступ через веб-интерфейс, адаптированный под настольные браузеры.",
        "",
        "## 4. Состав и содержание работ",
        "1. Анализ требований и уточнение объёма работ.",
        "2. Проектирование структуры и интерфейса.",
        "3. Разработка функционала (см. раздел 3.1).",
        "4. Тестирование и устранение ошибок.",
        "5. Приёмка по критериям раздела 7.",
        "",
        "## 5. Технологический стек и ограничения",
        a.get("stack", "—"),
        "",
        "## 6. Порядок разработки и контроль",
        f"**Сроки:** {a.get('deadline', '—')}",
        "Промежуточные результаты демонстрируются исполнителем по ходу работы.",
        "",
        "## 7. Критерии приёмки",
    ]
    lines += ["- " + c.strip() for c in a.get("acceptance", "").splitlines() if c.strip()]
    lines += [
        "",
        "## 8. Что не входит в объём работ",
        a.get("excluded", "Не оговорено отдельно."),
        "",
        "## 9. Гарантии и сопровождение",
        "Исполнитель устраняет дефекты, обнаруженные в течение срока, согласованного сторонами.",
        "",
        "---",
        f"ТЗ сгенерировано TZ Generator (ГОСТ-структура), {datetime.now().strftime('%d.%m.%Y')}",
    ]
    return "\n".join(lines)


@app.route("/")
def index():
    """Главная: выбор режима + пояснение схемы (бесплатно / ГОСТ 100 ₽)."""
    return render_template("index.html")


@app.route("/form/<mode>")
def form(mode):
    """Форма-опросник для выбранного режима (light / gost).

    gost — платный режим: пускаем только с меткой оплаты в cookie
    (ставится в payments.py после возврата с ЮMoney).
    """
    if mode not in ("light", "gost"):
        return redirect(url_for("index"))
    if mode == "gost" and not session.get("gost_paid"):
        return redirect("/gost/pay")
    return render_template("form.html", mode=mode)


@app.route("/example")
def example():
    """Пример готового облегчённого ТЗ — страница доверия и SEO."""
    return render_template("example.html")


@app.route("/generate", methods=["POST"])
def generate():
    """Принимаем форму, собираем ТЗ, отдаём на просмотр.

    Для gost без подтверждённой оплаты — возвращаем на страницу оплаты.
    """
    mode = request_mode()
    a = {
        "name": request.form.get("name", "").strip(),
        "what": request.form.get("what", "").strip(),
        "goal": request.form.get("goal", "").strip(),
        "users": request.form.get("users", "").strip(),
        "features": request.form.get("features", ""),
        "stack": request.form.get("stack", "").strip(),
        "deadline": request.form.get("deadline", "").strip(),
        "acceptance": request.form.get("acceptance", ""),
        "excluded": request.form.get("excluded", "").strip(),
        "customer": request.form.get("customer", "").strip(),
        "contractor": request.form.get("contractor", "").strip(),
        "budget": request.form.get("budget", "").strip(),
        # новые поля: тип проекта + типовые и скелетные блоки
        "project_type": request.form.get("project_type", "").strip(),
        "stories": request.form.get("stories", ""),
        "risks": request.form.get("risks", "").strip(),
        "integrations": request.form.get("integrations", "").strip(),
        "errors": request.form.get("errors", "").strip(),
        "style": request.form.get("style", "").strip(),
        "deliverables": request.form.get("deliverables", ""),
        "revisions": request.form.get("revisions", "").strip(),
        "client_provides": request.form.get("client_provides", "").strip(),
        "payment": request.form.get("payment", "").strip(),
        "support": request.form.get("support", "").strip(),
    }
    if not a["name"] or not a["what"]:
        return redirect(url_for("form", mode=mode))
    if mode == "gost" and not session.get("gost_paid"):
        # Платный режим: генерация без метки оплаты не проходит
        return redirect("/gost/pay")

    tz_id = secrets.token_hex(8)
    tz_text = build_light_tz(a) if mode == "light" else build_gost_tz(a)
    tz_text, polished = ai_polish(tz_text)
    TZ_STORE[tz_id] = {"mode": mode, "text": tz_text, "polished": polished}
    return redirect(url_for("result", tz_id=tz_id))


def request_mode() -> str:
    """Определяем режим формы: если mode не пришёл (gost-поток) — light."""
    m = request.form.get("mode", "light")
    return m if m in ("light", "gost") else "light"


@app.route("/result/<tz_id>")
def result(tz_id):
    """Просмотр готового ТЗ + кнопка скачивания .md."""
    item = TZ_STORE.get(tz_id)
    if not item:
        return redirect(url_for("index"))
    return render_template("result.html", tz_id=tz_id, text=item["text"],
                           mode=item["mode"], polished=item.get("polished", False))


@app.route("/download/<tz_id>")
def download(tz_id):
    """Скачивание ТЗ в формате .md."""
    item = TZ_STORE.get(tz_id)
    if not item:
        return redirect(url_for("index"))
    filename = f"tz_{item['mode']}_{tz_id[:6]}.md"
    notify.notify_download(item["mode"], tz_id[:6], "md")
    return Response(
        item["text"],
        mimetype="text/markdown",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


def _add_md_runs(paragraph, text):
    """Добавляем текст в параграф, **жирные** куски делаем bold-ранами."""
    for i, part in enumerate(text.split("**")):
        run = paragraph.add_run(part)
        if i % 2 == 1:
            run.bold = True


def tz_to_docx(text: str) -> bytes:
    """Конвертация markdown-ТЗ в .docx (python-docx).

    Понимаем нашу разметку: # заголовки, - списки, **жирный**, обычные абзацы.
    """
    import io
    from docx import Document
    doc = Document()
    for line in text.splitlines():
        s = line.strip()
        if not s or s == "---":
            continue
        if s.startswith("### "):
            doc.add_heading(s[4:].replace("**", ""), level=2)
        elif s.startswith("## "):
            doc.add_heading(s[3:].replace("**", ""), level=1)
        elif s.startswith("# "):
            doc.add_heading(s[2:].replace("**", ""), level=0)
        elif s.startswith("- "):
            p = doc.add_paragraph(style="List Bullet")
            _add_md_runs(p, s[2:])
        else:
            p = doc.add_paragraph()
            _add_md_runs(p, s)
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


@app.route("/download_docx/<tz_id>")
def download_docx(tz_id):
    """Скачивание ТЗ в формате .docx (собирается из серверной версии)."""
    item = TZ_STORE.get(tz_id)
    if not item:
        return redirect(url_for("index"))
    filename = f"tz_{item['mode']}_{tz_id[:6]}.docx"
    notify.notify_download(item["mode"], tz_id[:6], "docx")
    return Response(
        tz_to_docx(item["text"]),
        mimetype="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


# --- SEO: robots.txt и sitemap.xml ------------------------------------------------


@app.route("/robots.txt")
def robots():
    """Правила для поисковых роботов: сгенерированные документы не индексируем."""
    lines = [
        "User-agent: *",
        "Allow: /",
        "Disallow: /result/",
        "Disallow: /download/",
        f"Sitemap: {APP_URL}/sitemap.xml",
    ]
    return Response("\n".join(lines), mimetype="text/plain")


@app.route("/sitemap.xml")
def sitemap():
    """Карта сайта: статические публичные страницы."""
    urls = ["/", "/form/light", "/form/gost", "/example"]
    today = datetime.now().strftime("%Y-%m-%d")
    items = [
        f"<url><loc>{APP_URL}{u}</loc><lastmod>{today}</lastmod></url>"
        for u in urls
    ]
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        + "".join(items) + "</urlset>"
    )
    return Response(xml, mimetype="application/xml")


# --- ГОСТ-режим: оплата ЮMoney ------------------------------------------------
# Логика денег живёт в локальном модуле payments.py (в репозиторий не входит).
# Если модуля нет — /gost/pay отдаёт страницу-заглушку из того же шаблона.

try:
    from payments import bp as payments_bp
    app.register_blueprint(payments_bp)
except ImportError:

    @app.route("/gost/pay")
    def gost_pay_stub():
        return render_template("gost_pay.html", pay_url=None, label="")


if __name__ == "__main__":
    app.run(debug=True)