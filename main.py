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
    """Manages the system tray icon using lazy-loaded PIL and pystray."""

    def __init__(self):
        import pystray
        from PIL import Image, ImageDraw

        self.pystray = pystray
        self.Image = Image
        self.ImageDraw = ImageDraw
        self.icon = None
        self._create_icon()

    def _generate_image(self, color: str):
        """Generates a simple circle icon of the specified color."""
        width = 64
        height = 64
        image = self.Image.new('RGBA', (width, height), (0, 0, 0, 0))
        dc = self.ImageDraw.Draw(image)
        # Draw a colored circle with standard padding
        dc.ellipse((8, 8, 56, 56), fill=color)
        return image

    def _create_icon(self):
        self.icon = self.pystray.Icon("MagType")
        self.set_state_idle()

    def set_state_idle(self):
        """Gray icon: daemon is running and ready."""
        if self.icon:
            self.icon.icon = self._generate_image("gray")

    def set_state_listening(self):
        """Red icon: currently recording audio."""
        if self.icon:
            self.icon.icon = self._generate_image("red")

    def set_state_transcribing(self):
        """Green icon: processing audio with Whisper."""
        if self.icon:
            self.icon.icon = self._generate_image("green")

    def run(self):
        self.icon.run()


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