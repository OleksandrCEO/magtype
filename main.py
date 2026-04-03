import os
import sys
import socket
import threading
import subprocess
import argparse
import tempfile
import signal
from pathlib import Path

# --- Constants ---
SOCKET_PATH = "/tmp/magtype.sock"
AUDIO_SAMPLE_RATE = 16000


class ClipboardController:
    """Handles Wayland clipboard operations via wl-copy."""

    @staticmethod
    def paste_text(text: str):
        if not text:
            return

        try:
            subprocess.run(["wl-copy", text], check=True)
            try:
                # 29 is Ctrl, 47 is V. Pressing and releasing.
                subprocess.run(["ydotool", "key", "29:1", "47:1", "47:0", "29:0"], check=True)
            except Exception:
                pass

        except Exception as e:
            print(f"Clipboard operation failed: {e}")


class TrayIconManager:
    """Native KDE Tray Manager with Ctrl+C support via QTimer."""

    def __init__(self):
        from PyQt6.QtWidgets import QApplication, QSystemTrayIcon, QMenu
        from PyQt6.QtGui import QIcon
        from PyQt6.QtCore import QTimer
        from pathlib import Path
        import sys
        import os

        # Зберігаємо QIcon для доступу в інших методах класу
        self.QIcon = QIcon

        # 1. Ініціалізація Qt Application
        if not QApplication.instance():
            self.app = QApplication(sys.argv)
        else:
            self.app = QApplication.instance()

        # Щоб програма не закривалася при закритті вікон (яких у нас немає)
        self.app.setQuitOnLastWindowClosed(False)

        # 2. ХАК ДЛЯ CTRL+C
        # Таймер "прокидає" інтерпретатор Python кожні 500мс для обробки сигналів
        self.timer = QTimer()
        self.timer.start(500)
        self.timer.timeout.connect(lambda: None)

        # 3. ЛОГІКА ШЛЯХІВ (Nix Store safe)
        user_icons_path = Path.home() / ".config" / "magtype" / "icons"
        package_icons_path = Path(os.path.dirname(os.path.abspath(__file__))) / "icons"

        # Визначаємо, де брати/створювати іконки
        if user_icons_path.exists():
            # Якщо користувач поклав свої іконки в конфіг — беремо їх
            self.icons_dir = str(user_icons_path)
        elif os.access(os.path.dirname(os.path.abspath(__file__)), os.W_OK):
            # Якщо ми в звичайній папці з правами запису — використовуємо папку проєкту
            self.icons_dir = str(package_icons_path)
            os.makedirs(self.icons_dir, exist_ok=True)
        else:
            # Якщо ми в Nix Store (read-only) — створюємо іконки в ~/.config
            user_icons_path.mkdir(parents=True, exist_ok=True)
            self.icons_dir = str(user_icons_path)

        # 4. Завантаження іконок
        self.icons = {
            "idle": self._get_svg_icon("idle.svg", "#888888"),
            "listening": self._get_svg_icon("listening.svg", "#ff4444"),
            "transcribing": self._get_svg_icon("transcribing.svg", "#44ff44")
        }

        # 5. Створення Трей-меню
        self.tray = QSystemTrayIcon(self.icons["idle"])
        self.menu = QMenu()

        # Додаємо пункт виходу
        exit_action = self.menu.addAction("Вимкнути MagType")
        exit_action.triggered.connect(self.stop_all)

        self.tray.setContextMenu(self.menu)
        self.tray.show()

    def _get_svg_icon(self, filename: str, color: str):
        import os
        file_path = os.path.join(self.icons_dir, filename)
        if not os.path.exists(file_path):
            svg_content = f"""<?xml version="1.0" encoding="UTF-8"?>
            <svg width="64" height="64" viewBox="0 0 64 64" xmlns="http://www.w3.org/2000/svg">
                <circle cx="32" cy="32" r="24" fill="{color}" />
            </svg>"""
            with open(file_path, "w") as f:
                f.write(svg_content)
        return self.QIcon(file_path)

    def set_state_idle(self):
        if hasattr(self, 'tray'):
            self.tray.setIcon(self.icons["idle"])

    def set_state_listening(self):
        if hasattr(self, 'tray'):
            self.tray.setIcon(self.icons["listening"])

    def set_state_transcribing(self):
        if hasattr(self, 'tray'):
            self.tray.setIcon(self.icons["transcribing"])

    def stop_all(self):
        print("\n[+] Закриття через меню...")
        import os
        if os.path.exists(SOCKET_PATH):
            os.remove(SOCKET_PATH)
        os._exit(0)

    def run(self):
        self.app.exec()


class AudioRecorder:
    """Handles non-blocking audio recording."""

    def __init__(self, sample_rate: int = AUDIO_SAMPLE_RATE):
        import sounddevice as sd
        import numpy as np
        self.sd = sd
        self.np = np
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
        self.stream = self.sd.InputStream(
            samplerate=self.sample_rate,
            channels=1,
            callback=self._callback
        )
        self.stream.start()

    def stop(self) -> str:
        self.is_recording = False
        if self.stream:
            self.stream.stop()
            self.stream.close()

        if not self.audio_data:
            return ""

        import soundfile as sf
        recording = self.np.concatenate(self.audio_data, axis=0)
        temp_file = tempfile.NamedTemporaryFile(delete=False, suffix=".wav")
        sf.write(temp_file.name, recording, self.sample_rate)
        return temp_file.name


class MagTypeDaemon:
    """Main daemon process handling state, IPC, and models."""

    def __init__(self, config: argparse.Namespace, tray_manager: TrayIconManager):
        from faster_whisper import WhisperModel

        self.recorder = AudioRecorder()
        self.clipboard = ClipboardController()
        self.tray = tray_manager
        self.is_recording_state = False
        self.config = config

        # Setup config
        self.config_dir = Path.home() / ".config" / "magtype"
        self.config_dir.mkdir(parents=True, exist_ok=True)
        self.vocab_file = self.config_dir / "vocabulary.txt"
        self.vocabulary = self._load_vocabulary()

        print(f"Loading Whisper '{self.config.model}' on {self.config.device.upper()}...")
        compute_type = "float16" if self.config.device == "cuda" else "int8"
        self.model = WhisperModel(
            self.config.model,
            device=self.config.device,
            compute_type=compute_type
        )
        print("Daemon ready. Press Ctrl+C to stop.")

    def _load_vocabulary(self) -> str:
        if not self.vocab_file.exists():
            return ""
        return self.vocab_file.read_text(encoding="utf-8").replace("\n", ", ")

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
                threading.Thread(target=self._transcribe_and_type, args=(audio_path,), daemon=True).start()
            else:
                self.tray.set_state_idle()

    def _transcribe_and_type(self, audio_path: str):
        try:
            segments, info = self.model.transcribe(
                audio_path,
                beam_size=5,
                language=self.config.lang,
                initial_prompt=self.vocabulary if self.vocabulary else None
            )
            text = " ".join([segment.text.strip() for segment in segments]).strip()

            if text:
                self.clipboard.paste_text(text + " ")

        except Exception as e:
            print(f"Transcription failed: {e}")
        finally:
            if os.path.exists(audio_path):
                os.remove(audio_path)
            self.vocabulary = self._load_vocabulary()
            self.tray.set_state_idle()

    def start_socket_server(self):
        """Runs the Unix socket server to receive IPC commands."""
        if os.path.exists(SOCKET_PATH):
            os.remove(SOCKET_PATH)

        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        server.bind(SOCKET_PATH)
        server.listen(1)

        while True:
            try:
                conn, addr = server.accept()
                data = conn.recv(1024).decode('utf-8')
                if data == "TOGGLE":
                    self.handle_toggle()
                conn.close()
            except Exception:
                pass


def send_toggle_command():
    """Ultra-lightweight client: NO heavy imports here."""
    if not os.path.exists(SOCKET_PATH):
        print("Daemon is not running! Start it with --daemon")
        sys.exit(1)

    client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        client.connect(SOCKET_PATH)
        client.sendall(b"TOGGLE")
    except Exception as e:
        print(f"Failed to communicate with daemon: {e}")
    finally:
        client.close()


def shutdown_handler(signum, frame):
    """Handles Ctrl+C strictly and reliably."""
    print("\n[+] Shutting down MagType daemon...")
    if os.path.exists(SOCKET_PATH):
        os.remove(SOCKET_PATH)
    # os._exit cleanly destroys the process and DBus connection instantly,
    # preventing zombie tray icons in KDE Plasma.
    os._exit(0)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="MagType - Local AI Dictation")
    parser.add_argument("--daemon", action="store_true", help="Start the background daemon")
    parser.add_argument("--toggle", action="store_true", help="Toggle recording state")

    # Optional arguments for configuration (used only with --daemon)
    parser.add_argument("--lang", type=str, default="uk", help="Transcription language (e.g., uk, en)")
    parser.add_argument("--model", type=str, default="large-v3", help="Whisper model size (base, small, large-v3)")
    parser.add_argument("--device", type=str, default="cuda", choices=["cpu", "cuda"], help="Compute device (cpu, cuda)")

    args = parser.parse_args()

    if args.daemon:
        # Register immediate kill on Ctrl+C to clean up DBus states properly
        signal.signal(signal.SIGINT, shutdown_handler)
        signal.signal(signal.SIGTERM, shutdown_handler)

        tray = TrayIconManager()
        daemon = MagTypeDaemon(args, tray)

        # Socket server MUST run in a background thread
        socket_thread = threading.Thread(target=daemon.start_socket_server, daemon=True)
        socket_thread.start()

        # Tray icon MUST run in the main thread (blocking)
        tray.run()

    elif args.toggle:
        send_toggle_command()
    else:
        parser.print_help()