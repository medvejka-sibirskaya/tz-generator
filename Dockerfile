FROM python:3.11-slim
# Базовый образ — Python 3.11 slim (минор пиннёна); для полной
# воспроизводимости можно добавлять @sha256-дайджест конкретной сборки.

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONUTF8=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Не root: компрометация контейнера (уязвимый пакет, LFI) не отдаёт корневых прав.
# UID 1001 фиксирован, чтобы dokploy bind-mount журнала (/opt/tz-private/payments.log)
# можно было открыть на хосте: chown 1001 /opt/tz-private/payments.log
WORKDIR /app
RUN adduser --system --uid 1001 appuser && chown -R appuser /app
USER appuser

EXPOSE 5001

# Один воркер: TZ_STORE хранится в памяти процесса — при 2+ воркерах
# результат генерации может попасть к другому воркеру и не открыться.
# --timeout 120: LLM-полировка ходит до 60 с — дефолтные 30 с роняли воркер 502-м.
CMD ["gunicorn", "--workers", "1", "--threads", "8", "--timeout", "120", "--bind", "0.0.0.0:5001", "app:app"]

# Живая проверка: healthz отвечает, пока процессы сервиса живы
# (в теле ответа — bool о модулях платежей и секретах, значений не раскрываем).
HEALTHCHECK --interval=60s --timeout=5s --start-period=15s --retries=2 \
    CMD python -c "import urllib.request,sys; r=urllib.request.urlopen('http://127.0.0.1:5001/healthz', timeout=4); sys.exit(0 if r.status==200 else 1)"