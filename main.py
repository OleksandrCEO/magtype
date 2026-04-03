import os
import socket
import threading
import subprocess
import argparse
import tempfile

import numpy as np
import pystray
import sounddevice as sd
import soundfile as sf

from PIL import Image, ImageDraw
from pathlib import Path
from faster_whisper import WhisperModel

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
            # Copy to Wayland clipboard
            subprocess.run(["wl-copy", text], check=True)
            # Attempt to paste programmatically
            try:
                subprocess.run(["ydotool", "key", "29:1", "47:1", "47:0", "29:0"], check=True)
            except Exception:
                pass  # Fail silently if ydotoold is not configured yet
        except Exception as e:
            print(f"Clipboard operation failed: {e}")


class TrayIconManager:
    """Manages the system tray icon and its states."""

    def __init__(self):
        self.icon = None
        self._create_icon()

    def _generate_image(self, color: str, transparent: bool = False):
        """Generates a colored circle or a transparent pixel."""
        width = 64
        height = 64
        image = Image.new('RGBA', (width, height), (0, 0, 0, 0))

        if not transparent:
            dc = ImageDraw.Draw(image)
            dc.ellipse((8, 8, 56, 56), fill=color)

        return image

    def _create_icon(self):
        self.icon = pystray.Icon("MagType")
        self.set_state_idle()

    def set_state_idle(self):
        """Transparent state (hidden)."""
        if self.icon:
            self.icon.icon = self._generate_image("", transparent=True)

    def set_state_listening(self):
        """Red state (recording)."""
        if self.icon:
            self.icon.icon = self._generate_image("red")

    def set_state_transcribing(self):
        """Green state (transcribing)."""
        if self.icon:
            self.icon.icon = self._generate_image("green")

    def run(self):
        self.icon.run()


class AudioRecorder:
    """Handles non-blocking audio recording."""

    def __init__(self, sample_rate: int = AUDIO_SAMPLE_RATE):
        self.sample_rate = sample_rate
        self.is_recording = False
        self.audio_data = []
        self.stream = None

    def _callback(self, indata, frames, time, status):
        if self.is_recording:
            self.audio_data.append(indata.copy())

    def start(self):
        self.audio_data = []
        self.is_recording = True
        self.stream = sd.InputStream(
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

        recording = np.concatenate(self.audio_data, axis=0)
        temp_file = tempfile.NamedTemporaryFile(delete=False, suffix=".wav")
        sf.write(temp_file.name, recording, self.sample_rate)
        return temp_file.name


class MagTypeDaemon:
    """Main daemon process handling state, IPC, and models."""

    def __init__(self, tray_manager: TrayIconManager, config: argparse.Namespace):
        self.recorder = AudioRecorder()
        self.clipboard = ClipboardController()
        self.tray = tray_manager
        self.is_recording_state = False
        self.config = config

        # Setup config directory in ~/.config/magtype
        self.config_dir = Path.home() / ".config" / "magtype"
        self.config_dir.mkdir(parents=True, exist_ok=True)
        self.vocab_file = self.config_dir / "vocabulary.txt"

        self.vocabulary = self._load_vocabulary()

        print(f"Loading Whisper '{self.config.model}' on {self.config.device.upper()}...")
        # float16 is heavily optimized for modern NVIDIA GPUs (RTX series)
        compute_type = "float16" if self.config.device == "cuda" else "int8"

        self.model = WhisperModel(
            self.config.model,
            device=self.config.device,
            compute_type=compute_type
        )
        print("Daemon is ready.")

    def _load_vocabulary(self) -> str:
        """Loads vocabulary from ~/.config/magtype/vocabulary.txt"""
        if not self.vocab_file.exists():
            # Create a default file if it doesn't exist
            default_words = "MagType\nNixOS\nKDE Plasma\nPyCharm\nWayland\n"
            self.vocab_file.write_text(default_words, encoding="utf-8")
            return default_words.replace("\n", ", ")

        with open(self.vocab_file, "r", encoding="utf-8") as f:
            return f.read().replace("\n", ", ")

    def handle_toggle(self):
        """Switches between recording and transcribing states."""
        if not self.is_recording_state:
            self.is_recording_state = True
            self.tray.set_state_listening()
            self.recorder.start()
        else:
            self.is_recording_state = False
            self.tray.set_state_transcribing()

            audio_path = self.recorder.stop()
            if audio_path:
                threading.Thread(target=self._transcribe_and_type, args=(audio_path,)).start()
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
            # Reload vocabulary before next recording in case user edited the file
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
    """Client function to trigger the daemon."""
    if not os.path.exists(SOCKET_PATH):
        print("Daemon is not running. Start it with --daemon")
        return

    client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        client.connect(SOCKET_PATH)
        client.sendall(b"TOGGLE")
    except Exception as e:
        print(f"Failed to communicate with daemon: {e}")
    finally:
        client.close()


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
        tray = TrayIconManager()
        daemon = MagTypeDaemon(tray, args)

        socket_thread = threading.Thread(target=daemon.start_socket_server, daemon=True)
        socket_thread.start()

        tray.run()

    elif args.toggle:
        send_toggle_command()
    else:
        parser.print_help()