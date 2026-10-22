# -*- coding: utf-8 -*-
"""Админ-панель TZ Generator: журнал платежей + ручное открытие доступа.

Зачем: когда HTTP-уведомление ЮMoney не подтвердит оплату (задержка,
несовпадение секрета), доступ покупателю открывается вручную. Раньше для
этого нужен был SSH на сервер — теперь страничка /admin, доступная с
ПК и телефона.

С 21.10 ручной доступ — ВЕЧНАЯ ССЫЛКА /gost/access/<токен> (128 бит):
грант пишет confirmed-строку с токеном и показывает готовую ссылку покупателю.
Метку manual-... в URL больше не используем — она 8 hex-символов и работает
как пароль.

Секреты: ADMIN_TOKEN задаётся только в env (Dokploy UI → Environment),
в коде и в git его нет. Без ADMIN_TOKEN в env панель не пускает никого.
"""
import os
import re
import time
import json
import secrets
from datetime import datetime

from flask import Blueprint, render_template, request, redirect, url_for, session

try:
    import payments  # тот же процесс/журнал; модуль может отсутствовать вместе с оплатой
except ImportError:
    payments = None

bp = Blueprint("admin", __name__)

ADMIN_TOKEN = os.environ.get("ADMIN_TOKEN", "")
PAY_LOG = "payments.log"   # тот же журнал, что пишет payments.py (bind-mount в проде)
LABEL_RE = re.compile(r"^(tz-gost|manual)-[0-9a-zA-Z_-]{3,64}$")  # метки: наша и ручные
LOGIN_MAX_TRIES = 10       # неудачных входов с одного IP за час — потом блок до конца часа
_BAD_TRIES: dict = {}      # ip -> времена невалидных попыток (монотонные)


def _authorized() -> bool:
    return bool(ADMIN_TOKEN) and session.get("admin_ok") is True


def _login_rate_ok(ip: str) -> bool:
    """Скользящее окно на час по неудачным попыткам входа."""
    now = time.monotonic()
    tries = [t for t in _BAD_TRIES.get(ip, []) if now - t < 3600]
    _BAD_TRIES[ip] = tries
    if len(tries) >= LOGIN_MAX_TRIES:
        return False
    tries.append(now)
    return True


def _read_log() -> list:
    """Журнал платежей, свежие строки сверху; битые строки пропускаем."""
    rows = []
    try:
        with open(PAY_LOG, encoding="utf-8") as f:
            lines = f.read().splitlines()
    except OSError:
        return []
    for line in reversed(lines):
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows[:200]


@bp.route("/admin", methods=["GET", "POST"])
def admin():
    """Вход по токену + дашборд журнала платежей."""
    if not ADMIN_TOKEN:
        return render_template("admin.html", logged=False,
                               error="ADMIN_TOKEN не задан в env — панель закрыта")
    error = ""
    if request.method == "POST":
        token = request.form.get("token", "").strip()  # копипаст любит приносить пробел на конце
        try:
            # compare_digest на байтах: на не-ASCII строке со str он падает
            # TypeError'ом (500-страница на самом входе админки).
            passed = secrets.compare_digest(token.encode("utf-8"),
                                            ADMIN_TOKEN.encode("utf-8"))
        except UnicodeEncodeError:
            passed = False
        if passed:
            _BAD_TRIES.pop(request.remote_addr or "?", None)  # верный ключ — окно чистим
            session["admin_ok"] = True
        elif _login_rate_ok(request.remote_addr or "?"):
            error = "Неверный ключ"
        else:
            error = "Слишком много неверных попыток — попробуй через час"

    granted_token = session.pop("granted_token", "")  # ссылка после успешного гранта
    granted_link = f"/gost/access/{granted_token}" if granted_token else ""
    return render_template("admin.html", logged=_authorized(), rows=_read_log(),
                           error=error, granted_link=granted_link)


@bp.route("/admin/logout")
def admin_logout():
    session.pop("admin_ok", None)
    return redirect(url_for("admin.admin"))


@bp.route("/admin/grant", methods=["POST"])
def admin_grant():
    """Кнопка «Открыть доступ» по метке из журнала или введённой вручную."""
    if not _authorized():
        return redirect(url_for("admin.admin"))
    label = request.form.get("label", "").strip()
    from_label = request.form.get("from_label", "").strip()
    target = label or from_label  # с формы журнала приходит from_label, с ручной — label
    if payments is not None and LABEL_RE.fullmatch(target):
        token = payments.grant_access(target, "Открыто вручную через панель администратора")
        if token:
            # Ссылку показываем на следующей странице (через сессию, а не query —
            # она попадёт в историю браузера).
            session["granted_token"] = token
    return redirect(url_for("admin.admin"))