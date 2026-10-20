# -*- coding: utf-8 -*-
"""Админ-панель TZ Generator: журнал платежей + ручное открытие доступа.

Зачем: когда HTTP-уведомление ЮMoney не подтвердит оплату (задержка,
несовпадение секрета), доступ покупателю открывается вручную. Раньше для
этого нужен был SSH на сервер — теперь страничка /admin, доступная с
ПК и телефона.

Секреты: ADMIN_TOKEN задаётся только в env (Dokploy UI → Environment),
в коде и в git его нет. Без ADMIN_TOKEN в env панель не пускает никого.
"""
import os
import json
import secrets
from datetime import datetime

from flask import Blueprint, render_template, request, redirect, url_for, session

bp = Blueprint("admin", __name__)

ADMIN_TOKEN = os.environ.get("ADMIN_TOKEN", "")
PAY_LOG = "payments.log"   # тот же журнал, что пишет payments.py (bind-mount в проде)


def _authorized() -> bool:
    return bool(ADMIN_TOKEN) and session.get("admin_ok") is True


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


def _grant(label: str, reason: str) -> None:
    """Ручное подтверждение метки: строка confirmed со ссылкой на причину."""
    with open(PAY_LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps({
            "time": datetime.now().strftime("%d.%m.%Y %H:%M:%S"),
            "label": label,
            "status": "confirmed",
            "reason": reason,
        }, ensure_ascii=False) + "\n")


@bp.route("/admin", methods=["GET", "POST"])
def admin():
    """Вход по токену + дашборд журнала платежей."""
    if not ADMIN_TOKEN:
        return render_template("admin.html", logged=False,
                               error="ADMIN_TOKEN не задан в env — панель закрыта")
    error = ""
    if request.method == "POST":
        token = request.form.get("token", "")
        if secrets.compare_digest(token, ADMIN_TOKEN):
            session["admin_ok"] = True
        else:
            # compare_digest уже сравнивает; сюда попадаем только при промахе
            error = "Неверный ключ"
    return render_template("admin.html", logged=_authorized(), rows=_read_log(),
                           error=error)


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
    if target:
        _grant(target, "Открыто вручную через админ-панель Медвежки")
    return redirect(url_for("admin.admin"))