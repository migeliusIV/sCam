import os
import cv2
import time
import datetime
import json
import shutil
import signal
import logging
import subprocess
from pathlib import Path
from ultralytics import YOLO
from collections import defaultdict

# Настройка логирования
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger(__name__)

# Конфигурация из переменных окружения
CAMERA_SOURCE = os.getenv("CAMERA_SOURCE", "0")
BUFFER_DIR = Path(os.getenv("BUFFER_DIR", "/app/data/buffer"))
ARCHIVE_DIR = Path(os.getenv("ARCHIVE_DIR", "/app/data/permanent"))
TRACKS_FILE = BUFFER_DIR / "tracks.json"
CHUNK_DURATION_SEC = int(os.getenv("CHUNK_DURATION_SEC", "300"))
BUFFER_HOURS = int(os.getenv("BUFFER_HOURS", "48"))
TOLERANCE_MIN = int(os.getenv("TOLERANCE_MIN", "10"))
MODEL_SIZE = os.getenv("MODEL_SIZE", "yolov8n.pt")
FRAME_INTERVAL = int(os.getenv("FRAME_INTERVAL", "2"))
ARCHIVE_COOLDOWN_MIN = 30

BUFFER_DIR.mkdir(parents=True, exist_ok=True)
ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)

model = YOLO(MODEL_SIZE)
tracks = defaultdict(lambda: {"first_seen": None, "last_seen": None})
stop_event = False
last_archive_time = None

def init_camera(source):
    """Надёжное открытие камеры с явным форматом MJPG"""
    cap = cv2.VideoCapture(source)
    if not cap.isOpened():
        logger.error(f"Не удалось открыть камеру {source}")
        return None

    # 🔧 Явно задаём параметры (как в успешном ffmpeg-тесте)
    cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*'MJPG'))
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)
    cap.set(cv2.CAP_PROP_FPS, 30)
    cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)  # Минимальная задержка

    # 🔥 "Прогрев" камеры: первые 5-10 кадров после смены формата часто пустые
    logger.info("Инициализация камеры (прогрев)...")
    for _ in range(10):
        ok, _ = cap.read()
        if ok:
            logger.info("✅ Камера готова к работе")
            return cap
        time.sleep(0.05)

    logger.warning("⚠️ Камера открылась, но кадры не идут. Пробуем авто-формат...")
    # Фоллбэк: перезапуск без явного FOURCC
    cap.release()
    cap = cv2.VideoCapture(source)
    cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
    return cap if cap.isOpened() else None

def create_video_writer(output_path, width=1280, height=720, fps=30):
    """Создаёт VideoWriter для записи чанка в H.264"""
    fourcc = cv2.VideoWriter_fourcc(*'avc1')  # H.264
    writer = cv2.VideoWriter(output_path, fourcc, fps, (width, height))
    if not writer.isOpened():
        logger.warning(f"⚠️ VideoWriter не открылся, пробуем MJPG...")
        fourcc = cv2.VideoWriter_fourcc(*'MJPG')
        writer = cv2.VideoWriter(output_path, fourcc, fps, (width, height))
    return writer

def load_tracks():
    global tracks
    if TRACKS_FILE.exists():
        try:
            with open(TRACKS_FILE, "r") as f:
                data = json.load(f)
            for k, v in data.items():
                tid = int(k)
                tracks[tid]["first_seen"] = datetime.datetime.fromisoformat(v["first_seen"]) if v.get("first_seen") else None
                tracks[tid]["last_seen"] = datetime.datetime.fromisoformat(v["last_seen"]) if v.get("last_seen") else None
        except Exception as e:
            logger.error(f"Ошибка загрузки треков: {e}")

def save_tracks():
    try:
        temp_file = TRACKS_FILE.with_suffix(".tmp")
        with open(temp_file, "w") as f:
            json.dump({str(k): v for k, v in tracks.items()}, f, default=str)
        temp_file.rename(TRACKS_FILE)
    except Exception as e:
        logger.error(f"Ошибка сохранения треков: {e}")

def get_buffer_start_time():
    chunks = sorted(BUFFER_DIR.glob("chunk_*.mp4"))
    if not chunks:
        return datetime.datetime.now() - datetime.timedelta(hours=BUFFER_HOURS)
    ts_str = chunks[0].stem.replace("chunk_", "")
    try:
        return datetime.datetime.strptime(ts_str, "%Y%m%d_%H%M%S")
    except ValueError:
        return datetime.datetime.now() - datetime.timedelta(hours=BUFFER_HOURS)

def cleanup_buffer():
    cutoff = datetime.datetime.now() - datetime.timedelta(hours=BUFFER_HOURS)
    for chunk in BUFFER_DIR.glob("chunk_*.mp4"):
        ts_str = chunk.stem.replace("chunk_", "")
        try:
            chunk_time = datetime.datetime.strptime(ts_str, "%Y%m%d_%H%M%S")
            if chunk_time < cutoff:
                chunk.unlink()
                logger.info(f"[CLEANUP] Удалён старый чанк: {chunk.name}")
        except ValueError:
            continue

def check_archive_condition():
    global last_archive_time
    if last_archive_time:
        if datetime.datetime.now() - last_archive_time < datetime.timedelta(minutes=ARCHIVE_COOLDOWN_MIN):
            return False

    buf_start = get_buffer_start_time()
    now = datetime.datetime.now()
    tol = datetime.timedelta(minutes=TOLERANCE_MIN)

    for tid, log in tracks.items():
        if log["first_seen"] and log["last_seen"]:
            if (log["first_seen"] <= buf_start + tol) and (log["last_seen"] >= now - tol):
                logger.info(f"[ARCHIVE] ID {tid} присутствовал почти весь период. Сохраняем буфер.")
                archive_buffer()
                last_archive_time = now
                return True
    return False

def archive_buffer():
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    dest = ARCHIVE_DIR / f"archive_{ts}"
    dest.mkdir(exist_ok=True)
    for chunk in BUFFER_DIR.glob("chunk_*.mp4"):
        shutil.copy2(chunk, dest / chunk.name)
    shutil.copy2(TRACKS_FILE, dest / "tracks.json") if TRACKS_FILE.exists() else None
    logger.info(f"[ARCHIVE] Буфер сохранён в {dest}")

def start_ffmpeg_recording():
    cmd = [
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-i", CAMERA_SOURCE,
        "-c:v", "libx265", "-preset", "fast", "-crf", "28",
        "-f", "segment", "-segment_time", str(CHUNK_DURATION_SEC),
        "-segment_format", "mp4",
        "-strftime", "1",
        str(BUFFER_DIR / "chunk_%Y%m%d_%H%M%S.mp4")
    ]
    return subprocess.Popen(cmd)

def handle_signal(signum, frame):
    global stop_event
    logger.info("Получен сигнал остановки...")
    stop_event = True

def process_frames(cap):
    global stop_event
    frame_count = 0

    while not stop_event:
        ret, frame = cap.read()
        if not ret:
            logger.warning("Камера недоступна, попытка переподключения...")
            time.sleep(2)
            cap.open(CAMERA_SOURCE)
            continue

        frame_count += 1
        if frame_count % FRAME_INTERVAL != 0:
            continue

        results = model.track(frame, persist=True, verbose=False)
        now = datetime.datetime.now()

        if results[0].boxes.id is not None:
            for box_id in results[0].boxes.id:
                tid = int(box_id)
                if tracks[tid]["first_seen"] is None:
                    tracks[tid]["first_seen"] = now
                tracks[tid]["last_seen"] = now

        # Проверка и очистка ~каждые 30 секунд
        if frame_count % (FRAME_INTERVAL * 15) == 0:
            save_tracks()
            cleanup_buffer()
            check_archive_condition()

    cap.release()

if __name__ == "__main__":
    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    load_tracks()
    logger.info("📹 Запуск умного видеорегистратора в контейнере...")
    logger.info(f"Камера: {CAMERA_SOURCE} | Модель: {MODEL_SIZE} | Буфер: {BUFFER_HOURS}ч")

    # 🔹 Инициализация камеры (одна точка захвата)
    cap = init_camera(CAMERA_SOURCE)  # твоя функция с "прогревом"
    if cap is None:
        logger.error("Не удалось открыть камеру после всех попыток")
        exit(1)

    # 🔹 Параметры записи
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    fps = int(cap.get(cv2.CAP_PROP_FPS)) or 30

    writer = None
    chunk_start = None
    frame_count = 0
    chunk_frames = CHUNK_DURATION_SEC * fps

    logger.info(f"🎬 Запись: {width}x{height}@{fps}fps, чанки по {CHUNK_DURATION_SEC}с")

    try:
        while not stop_event:
            ret, frame = cap.read()
            if not ret:
                logger.warning("Камера недоступна, попытка переподключения...")
                time.sleep(2)
                cap = init_camera(CAMERA_SOURCE)
                if cap is None:
                    continue
                continue

            frame_count += 1

            # 🔹 Создание нового чанка
            if writer is None or frame_count % chunk_frames == 0:
                if writer:
                    writer.release()
                chunk_start = datetime.datetime.now()
                chunk_path = BUFFER_DIR / f"chunk_{chunk_start.strftime('%Y%m%d_%H%M%S')}.mp4"
                writer = create_video_writer(str(chunk_path), width, height, fps)
                logger.info(f"📦 Новый чанк: {chunk_path.name}")

            # 🔹 Запись кадра
            if writer and writer.isOpened():
                writer.write(frame)

            # 🔹 Детекция (с пропуском кадров)
            if frame_count % FRAME_INTERVAL == 0:
                results = model.track(frame, persist=True, verbose=False)
                now = datetime.datetime.now()
                if results[0].boxes.id is not None:
                    for box_id in results[0].boxes.id:
                        tid = int(box_id)
                        if tracks[tid]["first_seen"] is None:
                            tracks[tid]["first_seen"] = now
                        tracks[tid]["last_seen"] = now

            # 🔹 Периодические задачи
            if frame_count % (FRAME_INTERVAL * 15) == 0:
                save_tracks()
                cleanup_buffer()
                check_archive_condition()

    except Exception as e:
        logger.error(f"Критическая ошибка: {e}", exc_info=True)
    finally:
        logger.info("🛑 Остановка записи...")
        if writer:
            writer.release()
        if cap:
            cap.release()
        save_tracks()
        logger.info("✅ Работа завершена.")