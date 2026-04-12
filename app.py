from flask import Flask, render_template, Response, request, jsonify, send_from_directory
from flask_socketio import SocketIO
import threading
import os
import cv2
import time
import face_recognition

from config import AppConfig
from camera_processor import CameraProcessor

app = Flask(__name__)
app.config['SECRET_KEY'] = 'face-guard-secret-key-2025'

socketio = SocketIO(app, cors_allowed_origins="*", async_mode='threading')

processors = {}
known_face_encodings = []
known_face_names = []
events = []
events_lock = threading.Lock()


def load_known_faces():
    global known_face_encodings, known_face_names
    known_face_encodings.clear()
    known_face_names.clear()
    folder = AppConfig.KNOWN_FACES_DIR
    os.makedirs(folder, exist_ok=True)
    for filename in os.listdir(folder):
        if filename.lower().endswith(('.jpg', '.jpeg', '.png')):
            path = os.path.join(folder, filename)
            try:
                image = face_recognition.load_image_file(path)
                encodings = face_recognition.face_encodings(image)
                if encodings:
                    known_face_encodings.append(encodings[0])
                    known_face_names.append(os.path.splitext(filename)[0])
                    print(f"✅ Загружено: {filename}")
            except Exception as e:
                print(f"❌ Ошибка загрузки {filename}: {e}")


def add_event(event_data):
    with events_lock:
        events.append(event_data)
        if len(events) > 100:
            events.pop(0)


def init_processors():
    global processors
    load_known_faces()
    for idx in AppConfig.CAMERA_INDICES:
        proc = CameraProcessor(idx, socketio, known_face_encodings, known_face_names, add_event)
        processors[idx] = proc
        if AppConfig.CAMERA_ENABLED.get(idx, True):
            proc.start()


def gen_frames(cam_id):
    processor = processors.get(cam_id)
    if not processor:
        return
    while True:
        frame = processor.get_latest_frame()
        if frame is None:
            time.sleep(0.05)
            continue
        _, buffer = cv2.imencode('.jpg', frame)
        yield (b'--frame\r\n'
               b'Content-Type: image/jpeg\r\n\r\n' + buffer.tobytes() + b'\r\n')


@app.route('/')
def index():
    return render_template('index.html', cameras=AppConfig.CAMERA_INDICES)


@app.route('/video_feed/<int:cam_id>')
def video_feed(cam_id):
    if cam_id not in processors:
        return "Camera not found", 404
    return Response(gen_frames(cam_id), mimetype='multipart/x-mixed-replace; boundary=frame')


@app.route('/update_settings', methods=['POST'])
def update_settings():
    data = request.get_json() or {}
    try:
        for key, value in data.items():
            if key == 'scale_factor':
                AppConfig.SCALE_FACTOR = float(value)
            elif key == 'tolerance':
                AppConfig.TOLERANCE = float(value)
            elif key == 'notify_cooldown':
                AppConfig.NOTIFY_COOLDOWN = int(value)
            elif key == 'video_duration':
                AppConfig.VIDEO_DURATION = int(value)
            elif key == 'use_avi':
                AppConfig.USE_AVI = bool(value)
            elif key == 'camera_enabled':
                for c_id_str, enabled in value.items():
                    c_id = int(c_id_str)
                    if c_id in processors:
                        proc = processors[c_id]
                        enabled = bool(enabled)
                        AppConfig.CAMERA_ENABLED[c_id] = enabled

                        if enabled and not proc.is_running():
                            proc.start()
                        elif not enabled and proc.is_running():
                            proc.stop()

        return jsonify({'status': 'success'})
    except Exception as e:
        print(f"Ошибка в update_settings: {e}")
        return jsonify({'status': 'error', 'message': str(e)}), 500


@app.route('/alerts/<path:filename>')
def serve_alert(filename):
    return send_from_directory(AppConfig.ALERTS_DIR, filename)

@app.route('/events')
def events_page():
    with events_lock:
        sorted_events = sorted(events, key=lambda x: x.get('timestamp', ''), reverse=True)
    return render_template('events.html', events=sorted_events)

@app.route('/api/events')
def api_events():
    with events_lock:
        sorted_events = sorted(events, key=lambda x: x.get('timestamp', ''), reverse=True)
    return jsonify(sorted_events)

if __name__ == '__main__':
    os.makedirs(AppConfig.ALERTS_DIR, exist_ok=True)
    init_processors()
    print("="*70)
    print("🚀 Face Guard запущен!")
    print("🌐 http://127.0.0.1:5000")
    print("="*70)
    socketio.run(app, host='0.0.0.0', port=5000, debug=False, allow_unsafe_werkzeug=True)
