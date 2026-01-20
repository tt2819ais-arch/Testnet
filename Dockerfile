FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Создаем папку для логов
RUN mkdir -p /app/logs

CMD ["python", "main.py"]
