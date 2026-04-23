from flask import Flask, render_template, Response, request, jsonify, send_from_directory
from flask_socketio import SocketIO
import threading
import os
import cv2
import time
import face_recognition
import platform
import webbrowser
import sys

from config import AppConfig
from camera_processor import CameraProcessor
from logging_setup import setup_logging

app = Flask(__name__)
app.config['SECRET_KEY'] = 'face-guard-secret-key-2025'

socketio = SocketIO(app, cors_allowed_origins="*", async_mode='threading')

processors = {}
known_face_encodings = []
known_face_names = []
events = []
events_lock = threading.Lock()

def _runtime_base_dir() -> str:
    if getattr(sys, "frozen", False) and hasattr(sys, "executable"):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


def _make_abs_dir(path_like: str) -> str:
    if not path_like:
        return _runtime_base_dir()
    if os.path.isabs(path_like):
        return path_like
    return os.path.join(_runtime_base_dir(), path_like)


def init_runtime_paths():
    AppConfig.ALERTS_DIR = _make_abs_dir(getattr(AppConfig, "ALERTS_DIR", "alerts"))
    AppConfig.KNOWN_FACES_DIR = _make_abs_dir(getattr(AppConfig, "KNOWN_FACES_DIR", "known_faces"))


def get_camera_name(cam_id: int) -> str:
    name = getattr(AppConfig, "CAMERA_NAMES", {}).get(cam_id)
    if name:
        return str(name)
    return f"Камера {cam_id + 1}"

def get_windows_dshow_camera_names():
    try:
        from pygrabber.dshow_graph import FilterGraph
        graph = FilterGraph()
        names = graph.get_input_devices()
        if not isinstance(names, list):
            return []
        return [str(n) for n in names]
    except Exception:
        return []


def detect_available_cameras(max_index: int):
    available = []
    for cam_id in range(max_index + 1):
        cap = None
        try:
            cap = cv2.VideoCapture(cam_id, cv2.CAP_DSHOW)
            if cap is not None and cap.isOpened():
                available.append(cam_id)
        except Exception:
            pass
        finally:
            try:
                if cap is not None:
                    cap.release()
            except Exception:
                pass
    return available


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

def stop_all_processors():
    for cam_id, proc in list(processors.items()):
        try:
            proc.stop()
        except Exception as e:
            print(f"❌ Ошибка остановки камеры {cam_id}: {e}")


def init_processors():
    global processors
    load_known_faces()
    if getattr(AppConfig, "CAMERA_SCAN_MAX", None) is not None:
        if platform.system().lower().startswith("win"):
            dshow_names = get_windows_dshow_camera_names()
            if dshow_names:
                for idx, name in enumerate(dshow_names):
                    AppConfig.CAMERA_NAMES[idx] = name

        detected = detect_available_cameras(int(AppConfig.CAMERA_SCAN_MAX))
        if detected:
            AppConfig.CAMERA_INDICES = detected
            for idx in AppConfig.CAMERA_INDICES:
                AppConfig.CAMERA_ENABLED.setdefault(idx, True)
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

@app.route('/api/cameras')
def api_cameras():
    cams = []
    for cam_id in AppConfig.CAMERA_INDICES:
        cams.append({
            "id": cam_id,
            "name": get_camera_name(cam_id),
            "enabled": bool(AppConfig.CAMERA_ENABLED.get(cam_id, True))
        })
    return jsonify(cams)


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

@app.route('/api/shutdown', methods=['POST'])
def api_shutdown():
    allow_remote = bool(getattr(AppConfig, "ALLOW_REMOTE_SHUTDOWN", False))
    remote_addr = request.remote_addr or ""
    is_local = remote_addr in ("127.0.0.1", "::1")
    if not allow_remote and not is_local:
        return jsonify({"status": "error", "message": "Shutdown доступен только с этого ПК"}), 403

    shutdown_func = request.environ.get('werkzeug.server.shutdown')

    def shutdown_async(shutdown_callable):
        try:
            print("🛑 Остановка приложения по запросу с сайта...")
            stop_all_processors()
            time.sleep(0.3)

            if shutdown_callable:
                shutdown_callable()
                return
        except Exception as e:
            print(f"❌ Ошибка shutdown: {e}")

        time.sleep(0.5)
        os._exit(0)

    threading.Thread(target=shutdown_async, args=(shutdown_func,), daemon=True).start()
    return jsonify({"status": "success", "message": "Останавливаю приложение..."})

if __name__ == '__main__':
    setup_logging("face_guard.log")
    init_runtime_paths()
    os.makedirs(AppConfig.ALERTS_DIR, exist_ok=True)
    init_processors()
    print("="*70)
    print("🚀 Face Guard запущен!")
    print("🌐 http://127.0.0.1:5000")
    print("="*70)

    if bool(getattr(AppConfig, "AUTO_OPEN_BROWSER", True)):
        def _open_browser():
            time.sleep(1.0)
            try:
                webbrowser.open("http://127.0.0.1:5000", new=2)
            except Exception:
                pass

        threading.Thread(target=_open_browser, daemon=True).start()

    socketio.run(app, host='0.0.0.0', port=5000, debug=False, allow_unsafe_werkzeug=True)
