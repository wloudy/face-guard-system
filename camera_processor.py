import cv2
import face_recognition
import time
import os
import threading
from datetime import datetime
from discord_webhook import DiscordWebhook, DiscordEmbed

from config import AppConfig

face_recognition_lock = threading.Lock()


class CameraProcessor:
    def __init__(self, cam_id: int, socketio, known_face_encodings, known_face_names, event_callback=None):
        self.cam_id = cam_id
        self.socketio = socketio
        self.known_face_encodings = known_face_encodings
        self.known_face_names = known_face_names
        self.event_callback = event_callback

        self.cap = None
        self.running = False
        self.thread = None
        self.latest_frame = None
        self.frame_lock = threading.Lock()
        self.last_notification = 0

        os.makedirs(AppConfig.ALERTS_DIR, exist_ok=True)

    def start(self):
        if self.running: return
        self.running = True
        self.thread = threading.Thread(target=self._process_loop, daemon=True)
        self.thread.start()
        print(f"[CAM {self.cam_id}] Поток запущен")

    def stop(self):
        self.running = False
        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=3)
        if self.cap:
            self.cap.release()
        print(f"[CAM {self.cam_id}] Поток остановлен")

    def is_running(self):
        return self.running

    def get_latest_frame(self):
        with self.frame_lock:
            return self.latest_frame.copy() if self.latest_frame is not None else None

    def _process_loop(self):
        for backend in [cv2.CAP_DSHOW, cv2.CAP_MSMF, cv2.CAP_ANY]:
            self.cap = cv2.VideoCapture(self.cam_id, backend)
            if self.cap.isOpened():
                print(f"[CAM {self.cam_id}] Открыта с backend: {backend}")
                break

        if not self.cap or not self.cap.isOpened():
            print(f"[CAM {self.cam_id}] ❌ Не удалось открыть камеру")
            self.running = False
            return

        width = int(self.cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        height = int(self.cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        print(f"[CAM {self.cam_id}] Разрешение: {width}x{height}")

        while self.running:
            if not AppConfig.CAMERA_ENABLED.get(self.cam_id, False):
                time.sleep(0.3)
                continue

            ret, frame = self.cap.read()
            if not ret or frame is None:
                time.sleep(0.05)
                continue

            display_frame = frame.copy()

            scale = AppConfig.SCALE_FACTOR
            small_frame = cv2.resize(frame, (0, 0), fx=scale, fy=scale)
            rgb_small = cv2.cvtColor(small_frame, cv2.COLOR_BGR2RGB)

            with face_recognition_lock:
                face_locations = face_recognition.face_locations(rgb_small)
                face_encodings = face_recognition.face_encodings(rgb_small, face_locations)

            unknown_detected = False

            for (top, right, bottom, left), encoding in zip(face_locations, face_encodings):
                top = int(top / scale)
                right = int(right / scale)
                bottom = int(bottom / scale)
                left = int(left / scale)

                matches = face_recognition.compare_faces(self.known_face_encodings, encoding, tolerance=AppConfig.TOLERANCE)
                name = "Unknown"
                color = (0, 0, 255)
                if True in matches:
                    name = self.known_face_names[matches.index(True)]
                    color = (0, 255, 0)

                cv2.rectangle(display_frame, (left, top), (right, bottom), color, 3)
                cv2.putText(display_frame, name, (left + 6, top - 10), cv2.FONT_HERSHEY_SIMPLEX, 0.85, color, 2)

                if name == "Unknown":
                    unknown_detected = True

            display_frame = cv2.resize(display_frame, (AppConfig.WINDOW_WIDTH, AppConfig.WINDOW_HEIGHT))

            with self.frame_lock:
                self.latest_frame = display_frame.copy()

            if unknown_detected and (time.time() - self.last_notification > AppConfig.NOTIFY_COOLDOWN):
                self.last_notification = time.time()
                threading.Thread(
                    target=self._handle_unknown_detection,
                    args=(display_frame.copy(), width, height),
                    daemon=True
                ).start()

            time.sleep(0.03)

    def _handle_unknown_detection(self, display_frame, orig_width, orig_height):
        print(f"[CAM {self.cam_id}] 🚨 Неизвестное лицо обнаружено!")

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        cam_str = f"cam{self.cam_id}"

        photo_filename = f"photo_{cam_str}_{timestamp}.jpg"
        video_discord_filename = f"video_discord_{cam_str}_{timestamp}.avi"  # лёгкое для Discord
        video_full_filename = f"video_full_{cam_str}_{timestamp}.avi"  # качественное для сайта

        photo_path = os.path.join(AppConfig.ALERTS_DIR, photo_filename)
        video_discord_path = os.path.join(AppConfig.ALERTS_DIR, video_discord_filename)
        video_full_path = os.path.join(AppConfig.ALERTS_DIR, video_full_filename)

        try:
            cv2.imwrite(photo_path, display_frame)

            print(f"[CAM {self.cam_id}] 🎥 Запись лёгкого видео для Discord...")
            fourcc = cv2.VideoWriter_fourcc(*'MJPG')
            out_discord = cv2.VideoWriter(video_discord_path, fourcc, 15.0, (640, 360))

            frames_discord = 0
            start = time.time()
            while time.time() - start < AppConfig.VIDEO_DURATION and self.running:
                ret, f = self.cap.read()
                if ret and f is not None:
                    small = cv2.resize(f, (640, 360))
                    out_discord.write(small)
                    frames_discord += 1
            out_discord.release()

            print(f"[CAM {self.cam_id}] 🎥 Запись качественного видео для сайта...")
            out_full = cv2.VideoWriter(video_full_path, fourcc, 25.0, (orig_width, orig_height))

            frames_full = 0
            start = time.time()
            while time.time() - start < AppConfig.VIDEO_DURATION and self.running:
                ret, f = self.cap.read()
                if ret and f is not None:
                    out_full.write(f)
                    frames_full += 1
            out_full.release()

            print(f"[CAM {self.cam_id}] ✅ Лёгкое: {frames_discord} кадров | Качественное: {frames_full} кадров")

            webhook = DiscordWebhook(url=AppConfig.DISCORD_WEBHOOK_URL,
                                     username=f"Face Guard (Камера {self.cam_id + 1})")
            embed = DiscordEmbed(
                title=f"🚨 UNKNOWN FACE — Камера {self.cam_id + 1}",
                description="Обнаружен незнакомец",
                color=0xff0000
            )
            embed.set_timestamp()
            webhook.add_embed(embed)

            with open(photo_path, "rb") as f:
                webhook.add_file(file=f.read(), filename=photo_filename)
            webhook.execute()

            print(f"[CAM {self.cam_id}] ✅ Фото отправлено в Discord")

            if os.path.exists(video_discord_path) and frames_discord > 15:
                size_mb = os.path.getsize(video_discord_path) / (1024 * 1024)
                if size_mb < 8.0:
                    webhook_video = DiscordWebhook(url=AppConfig.DISCORD_WEBHOOK_URL,
                                                   username=f"Face Guard (Камера {self.cam_id + 1})")
                    webhook_video.content = f"📹 Видео с камеры {self.cam_id + 1} ({AppConfig.VIDEO_DURATION} сек)"
                    with open(video_discord_path, "rb") as f:
                        webhook_video.add_file(file=f.read(), filename=video_discord_filename)
                    webhook_video.execute()
                    print(f"[CAM {self.cam_id}] ✅ Лёгкое видео отправлено в Discord")

            event_data = {
                'timestamp': datetime.now().strftime("%H:%M:%S"),
                'cam_id': self.cam_id,
                'cam_name': f"Камера {self.cam_id + 1}",
                'photo': photo_filename,
                'video': video_full_filename,
                'message': 'Обнаружен Unknown!'
            }
            self.socketio.emit('new_event', event_data)
            if self.event_callback:
                self.event_callback(event_data)

            print(f"[CAM {self.cam_id}] ✅ Событие отправлено на сайт")

        except Exception as e:
            print(f"[CAM {self.cam_id}] Ошибка обработки тревоги: {e}")
