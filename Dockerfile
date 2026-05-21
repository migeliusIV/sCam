FROM python:3.10-slim

# Системные зависимости для OpenCV и FFmpeg
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    libgl1 \
    libglib2.0-0 \
    libv4l-0 \
    && rm -rf /var/lib/apt/lists/*
    
WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Предзагрузка модели, чтобы не ждать при первом старте
RUN python -c "from ultralytics import YOLO; YOLO('yolov8n.pt')"

COPY smart_recorder.py .

RUN mkdir -p /app/data/buffer /app/data/permanent

# Запуск приложения
CMD ["python", "smart_recorder.py"]