import os
import sys
import socket
import threading
import argparse
import tempfile
import signal
from pathlib import Path

from core.clipboard import ClipboardController
from core.icons import IconManager, get_socket_path

# --- Constants ---
SOCKET_PATH = get_socket_path()
AUDIO_SAMPLE_RATE = 16000

SHARED_MODEL_DIR = "/var/lib/magtype/models"
USER_MODEL_DIR = os.path.expanduser("~/.cache/magtype/models")


class TrayIconManager:
    """Manages the system tray icon and language selection menu."""

    def __init__(self, config: argparse.Namespace):
        from PyQt6.QtWidgets import QApplication, QSystemTrayIcon, QMenu
        from PyQt6.QtGui import QIcon, QAction, QActionGroup
        from PyQt6.QtCore import QTimer

        self.config = config
        self.app = QApplication.instance() or QApplication(sys.argv)
        self.app.setQuitOnLastWindowClosed(False)

        # Wake up Python interpreter to handle SIGINT
        self.timer = QTimer()
        self.timer.start(500)
        self.timer.timeout.connect(lambda: None)

        # Cross-platform icon management
        self.icon_manager = IconManager()
        icon_paths = self.icon_manager.get_all_icons()

        self.icons = {
            "idle": QIcon(icon_paths["idle"]),
            "listening": QIcon(icon_paths["listening"]),
            "transcribing": QIcon(icon_paths["transcribing"])
        }

        self.tray = QSystemTrayIcon(self.icons["idle"])
        self.menu = QMenu()

        # --- Language Selection Submenu ---
        self.lang_menu = self.menu.addMenu("Language")
        self.lang_group = QActionGroup(self.lang_menu)
        self.lang_group.setExclusive(True)

        # Define supported languages (None means Auto)
        languages = [("Auto detect", None), ("Ukrainian", "uk"), ("Russian", "ru"), ("English", "en")]

        for label, code in languages:
            action = QAction(label, self.lang_menu)
            action.setCheckable(True)
            action.setData(code)
            if self.config.lang == code:
                action.setChecked(True)

            action.triggered.connect(self._on_lang_changed)
            self.lang_group.addAction(action)
            self.lang_menu.addAction(action)

        self.menu.addSeparator()
        exit_action = self.menu.addAction("Exit MagType")
        exit_action.triggered.connect(self.stop_all)

        self.tray.setContextMenu(self.menu)
        self.tray.show()

    def _on_lang_changed(self):
        """Updates the daemon config when a user selects a language in the UI."""
        action = self.lang_group.checkedAction()
        new_lang = action.data()
        self.config.lang = new_lang
        status = new_lang if new_lang else "Auto-detect"
        print(f"[UI] Language switched to: {status}")

    def set_state_idle(self):
        self.tray.setIcon(self.icons["idle"])

    def set_state_listening(self):
        self.tray.setIcon(self.icons["listening"])

    def set_state_transcribing(self):
        self.tray.setIcon(self.icons["transcribing"])

    def stop_all(self):
        if os.path.exists(SOCKET_PATH):
            os.remove(SOCKET_PATH)
        os._exit(0)

    def run(self):
        self.app.exec()


class AudioRecorder:
    """Handles audio capture."""

    def __init__(self, sample_rate: int = AUDIO_SAMPLE_RATE):
        import sounddevice as sd
        import numpy as np
        self.sd, self.np = sd, np
        self.sample_rate = sample_rate
        self.is_recording = False
        self.audio_data = []
        self.stream = None

    def _callback(self, indata, frames, time, status):
        if self.is_recording:
            self.audio_data.append(self.np.copy(indata))

    def start(self):
        self.audio_data = []
        self.is_recording = True
        self.stream = self.sd.InputStream(samplerate=self.sample_rate, channels=1, callback=self._callback)
        self.stream.start()

    def stop(self) -> str:
        self.is_recording = False
        if self.stream:
            self.stream.stop()
            self.stream.close()
        if not self.audio_data: return ""

        import soundfile as sf
        recording = self.np.concatenate(self.audio_data, axis=0)
        temp_file = tempfile.NamedTemporaryFile(delete=False, suffix=".wav")
        sf.write(temp_file.name, recording, self.sample_rate)
        return temp_file.name


class MagTypeDaemon:
    """Daemon process managing models and IPC."""

    def __init__(self, config: argparse.Namespace, tray_manager: TrayIconManager):
        from faster_whisper import WhisperModel
        self.recorder = AudioRecorder()
        self.clipboard = ClipboardController()
        self.tray = tray_manager
        self.config = config
        self.is_recording_state = False

        self.vocab_file = Path.home() / ".config" / "magtype" / "vocabulary.txt"
        self.vocabulary = self._load_vocabulary()

        try:
            os.makedirs(SHARED_MODEL_DIR, exist_ok=True)
            download_dir = SHARED_MODEL_DIR

        except PermissionError:
            print(f"[!] No permission to write to {SHARED_MODEL_DIR}. Using local cache.")
            os.makedirs(USER_MODEL_DIR, exist_ok=True)
            download_dir = USER_MODEL_DIR

        print(f"Initializing Whisper ({self.config.model}) on {self.config.device}...")

        self.model = WhisperModel(
            self.config.model,
            device=self.config.device,
            compute_type="float16" if self.config.device == "cuda" else "int8",
            download_root=download_dir
        )

    def _load_vocabulary(self) -> str:
        return self.vocab_file.read_text(encoding="utf-8").replace("\n", ", ") if self.vocab_file.exists() else ""

    def handle_toggle(self):
        if not self.is_recording_state:
            self.is_recording_state = True
            self.tray.set_state_listening()
            self.recorder.start()
        else:
            self.is_recording_state = False
            self.tray.set_state_transcribing()
            audio_path = self.recorder.stop()
            if audio_path:
                threading.Thread(target=self._transcribe, args=(audio_path,), daemon=True).start()
            else:
                self.tray.set_state_idle()

    def _transcribe(self, audio_path: str):
        try:
            # language=None triggers auto-detection in faster-whisper
            segments, info = self.model.transcribe(
                audio_path,
                beam_size=5,
                language=self.config.lang,
                initial_prompt=self.vocabulary or None
            )

            if self.config.lang is None:
                print(f"[AI] Detected language: {info.language} ({info.language_probability:.2f})")

            text = " ".join([s.text.strip() for s in segments]).strip()
            if text:
                self.clipboard.paste_text(text + " ")
        except Exception as e:
            print(f"Error: {e}")
        finally:
            if os.path.exists(audio_path): os.remove(audio_path)
            self.vocabulary = self._load_vocabulary()
            self.tray.set_state_idle()

    def start_socket_server(self):
        if os.path.exists(SOCKET_PATH): os.remove(SOCKET_PATH)
        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        server.bind(SOCKET_PATH)
        server.listen(5)
        while True:
            try:
                conn, _ = server.accept()
                if conn.recv(1024).decode('utf-8') == "TOGGLE":
                    self.handle_toggle()
                conn.close()
            except:
                pass


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="MagType - Local AI Dictation")
    parser.add_argument("--daemon", action="store_true")
    parser.add_argument("--toggle", action="store_true")
    # Default is None for Auto-detection
    parser.add_argument("--lang", type=str, default=None, help="Force language (uk, en). Default: Auto")
    parser.add_argument("--model", type=str, default="large-v3")
    parser.add_argument("--device", type=str, default="cuda")

    args = parser.parse_args()

    if args.daemon:
        signal.signal(signal.SIGINT, lambda s, f: os._exit(0))
        tray = TrayIconManager(args)
        daemon = MagTypeDaemon(args, tray)
        threading.Thread(target=daemon.start_socket_server, daemon=True).start()
        tray.run()
    elif args.toggle:
        if not os.path.exists(SOCKET_PATH):
            sys.exit(1)
        client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        client.connect(SOCKET_PATH)
        client.sendall(b"TOGGLE")
        client.close()
    else:
        parser.print_help()