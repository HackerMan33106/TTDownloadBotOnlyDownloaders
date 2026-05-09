# ============================================
# Stage 1: Base image with dependencies
# Этот слой кэшируется и пересобирается только при изменении requirements.txt
# ============================================
FROM python:3.11.0 as base

# Устанавливаем системные зависимости (ffmpeg для извлечения аудио)
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Обновляем pip до последней версии
RUN pip install --no-cache-dir --upgrade pip==25.3

# Копируем только requirements.txt (для кэширования слоя)
COPY requirements.txt .

# Устанавливаем Python зависимости
# Этот слой будет закэширован и переиспользован при следующих сборках
RUN pip install --no-cache-dir -r requirements.txt

# ============================================
# Stage 2: Final image with bot code
# Этот слой пересобирается при каждом изменении кода (~5-10 сек)
# ============================================
FROM base

WORKDIR /app

# Копируем весь код бота
COPY . .

# Настраиваем директорию данных
ENV DATA_DIR=/app/data

# Запускаем бота
CMD ["python", "main.py"]