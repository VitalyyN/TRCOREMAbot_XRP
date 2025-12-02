FROM python:3.13-slim

WORKDIR /app

# Установка зависимостей
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Копирование ВСЕХ файлов, кроме указанных в .dockerignore
COPY . .

# Точка входа
CMD ["python", "main.py"]