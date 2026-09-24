FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 5001
# Один воркер: TZ_STORE хранится в памяти процесса — при 2+ воркерах
# результат генерации может попасть к другому воркеру и не открыться.
CMD ["gunicorn", "--workers", "1", "--threads", "8", "--bind", "0.0.0.0:5001", "app:app"]