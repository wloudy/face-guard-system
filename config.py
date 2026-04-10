import os

class AppConfig:
    DISCORD_WEBHOOK_URL = "Ваша ссылка на Discrod Webhook"

    KNOWN_FACES_DIR = "known_faces"
    ALERTS_DIR = "alerts"

    CAMERA_INDICES = [0, 1]
    CAMERA_ENABLED = {idx: True for idx in CAMERA_INDICES}

    SCALE_FACTOR = 0.25
    TOLERANCE = 0.55
    NOTIFY_COOLDOWN = 35
    VIDEO_DURATION = 6

    USE_AVI = True
    WINDOW_WIDTH = 640
    WINDOW_HEIGHT = 480

    MAX_EVENTS = 100
