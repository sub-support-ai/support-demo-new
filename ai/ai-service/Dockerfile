FROM python:3.12-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Исходники ai-service (без .venv, кэша и тестов — см. .dockerignore)
COPY . .

EXPOSE 8001

# main:app — без пакетного префикса: main.py лежит прямо в /app.
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8001"]
