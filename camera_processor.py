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
        if self.running:
            return
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
        print(f"[CAM {self.cam_id}] Остановлен")

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

        self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
        self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)

        while self.running:
            if not AppConfig.CAMERA_ENABLED.get(self.cam_id, False):
                time.sleep(0.3)
                continue

            ret, frame = self.cap.read()
            if not ret or frame is None:
                time.sleep(0.1)
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
                self._handle_unknown_detection(display_frame.copy())   # копия, чтобы не блокировать

            time.sleep(0.03)

    def _handle_unknown_detection(self, frame):
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        cam_str = f"cam{self.cam_id}"
        photo_filename = f"photo_{cam_str}_{timestamp}.jpg"
        video_filename = f"video_{cam_str}_{timestamp}.avi"
        photo_path = os.path.join(AppConfig.ALERTS_DIR, photo_filename)
        video_path = os.path.join(AppConfig.ALERTS_DIR, video_filename)

        cv2.imwrite(photo_path, frame)

        fourcc = cv2.VideoWriter_fourcc(*'MJPG')
        out = cv2.VideoWriter(video_path, fourcc, 15.0, (AppConfig.WINDOW_WIDTH, AppConfig.WINDOW_HEIGHT))

        frames_written = 0
        start_time = time.time()

        while time.time() - start_time < AppConfig.VIDEO_DURATION and self.running:
            ret, f = self.cap.read()
            if ret and f is not None:
                small = cv2.resize(f, (AppConfig.WINDOW_WIDTH, AppConfig.WINDOW_HEIGHT))
                out.write(small)
                frames_written += 1
        out.release()

        print(f"[CAM {self.cam_id}] Записано кадров: {frames_written}")

        try:
            webhook = DiscordWebhook(url=AppConfig.DISCORD_WEBHOOK_URL, username=f"Face Guard (Камера {self.cam_id + 1})")
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

            if os.path.exists(video_path) and frames_written > 20:
                webhook_video = DiscordWebhook(url=AppConfig.DISCORD_WEBHOOK_URL, username=f"Face Guard (Камера {self.cam_id + 1})")
                webhook_video.content = f"📹 Видео с камеры {self.cam_id + 1} ({AppConfig.VIDEO_DURATION} сек)"
                with open(video_path, "rb") as f:
                    webhook_video.add_file(file=f.read(), filename=video_filename)
                webhook_video.execute()

        except Exception as e:
            print(f"[CAM {self.cam_id}] Discord error: {e}")

        event_data = {
            'timestamp': datetime.now().strftime("%H:%M:%S"),
            'cam_id': self.cam_id,
            'cam_name': f"Камера {self.cam_id + 1}",
            'photo': photo_filename,
            'video': video_filename if os.path.exists(video_path) else None,
            'message': 'Обнаружен Unknown!'
        }

        self.socketio.emit('new_event', event_data)
        if self.event_callback:
            self.event_callback(event_data)
