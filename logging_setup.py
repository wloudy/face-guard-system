import logging
import os
import sys
import threading
from datetime import datetime


class _TeeStream:
    def __init__(self, *streams):
        self._streams = [s for s in streams if s is not None]
        self._lock = threading.Lock()

    def write(self, data):
        if not data:
            return 0
        with self._lock:
            for s in self._streams:
                try:
                    s.write(data)
                except Exception:
                    pass
            for s in self._streams:
                try:
                    s.flush()
                except Exception:
                    pass
        return len(data)

    def flush(self):
        with self._lock:
            for s in self._streams:
                try:
                    s.flush()
                except Exception:
                    pass


def setup_logging(log_path: str = "face_guard.log"):
    
    os.makedirs(os.path.dirname(os.path.abspath(log_path)) or ".", exist_ok=True)

    log_file = open(log_path, "w", encoding="utf-8", buffering=1)
    log_file.write("=" * 80 + "\n")
    log_file.write(f"Face Guard start: {datetime.now().isoformat(sep=' ', timespec='seconds')}\n")
    log_file.write("=" * 80 + "\n")

    root = logging.getLogger()
    root.setLevel(logging.INFO)
    for h in list(root.handlers):
        root.removeHandler(h)

    fmt = logging.Formatter("%(asctime)s | %(levelname)s | %(message)s", "%H:%M:%S")

    file_handler = logging.StreamHandler(log_file)
    file_handler.setFormatter(fmt)
    file_handler.setLevel(logging.INFO)

    console_handler = logging.StreamHandler(sys.__stdout__)
    console_handler.setFormatter(fmt)
    console_handler.setLevel(logging.INFO)

    root.addHandler(file_handler)
    root.addHandler(console_handler)

    sys.stdout = _TeeStream(sys.__stdout__, log_file)
    sys.stderr = _TeeStream(sys.__stderr__, log_file)

    return log_file

