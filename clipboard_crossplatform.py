"""
Cross-platform clipboard controller for MagType.

This module provides clipboard and keyboard simulation support for:
- Linux (Wayland via wl-copy + ydotool)
- Linux (X11 via xclip + xdotool)
- macOS (pbcopy + osascript)

To use: Replace ClipboardController in main.py with this implementation.
"""

import os
import platform
import subprocess
import shutil


class ClipboardController:
    """Cross-platform clipboard operations and virtual key injection."""

    def __init__(self):
        self.system = platform.system()
        self.is_wayland = bool(os.environ.get("WAYLAND_DISPLAY"))
        self._check_dependencies()

    def _check_dependencies(self):
        """Verify required tools are installed."""
        if self.system == "Darwin":
            required = ["pbcopy", "osascript"]
        elif self.system == "Linux":
            if self.is_wayland:
                required = ["wl-copy", "ydotool"]
            else:
                required = ["xclip", "xdotool"]
        else:
            raise OSError(f"Unsupported platform: {self.system}")

        missing = [cmd for cmd in required if not shutil.which(cmd)]
        if missing:
            raise RuntimeError(
                f"Missing required tools: {', '.join(missing)}\n"
                f"Please install them for your platform."
            )

    def copy_to_clipboard(self, text: str) -> bool:
        """Copy text to system clipboard."""
        if not text:
            return False

        try:
            if self.system == "Darwin":
                subprocess.run(
                    ["pbcopy"],
                    input=text.encode("utf-8"),
                    check=True
                )
            elif self.is_wayland:
                subprocess.run(
                    ["wl-copy", text],
                    check=True
                )
            else:  # X11
                subprocess.run(
                    ["xclip", "-selection", "clipboard"],
                    input=text.encode("utf-8"),
                    check=True
                )
            return True
        except subprocess.CalledProcessError as e:
            print(f"Clipboard copy failed: {e}")
            return False

    def simulate_paste(self) -> bool:
        """Simulate Ctrl+V (or Cmd+V on macOS) keypress."""
        try:
            if self.system == "Darwin":
                # macOS: Cmd+V via AppleScript
                subprocess.run([
                    "osascript", "-e",
                    'tell application "System Events" to keystroke "v" using command down'
                ], check=True)

            elif self.is_wayland:
                # Wayland: ydotool with scancodes
                # 29 = Left Ctrl, 47 = V
                # Format: scancode:pressed (1=down, 0=up)
                subprocess.run([
                    "ydotool", "key",
                    "29:1", "47:1", "47:0", "29:0"
                ], check=True)

            else:  # X11
                subprocess.run([
                    "xdotool", "key", "ctrl+v"
                ], check=True)

            return True
        except subprocess.CalledProcessError as e:
            print(f"Paste simulation failed: {e}")
            return False

    def paste_text(self, text: str):
        """Copy text to clipboard and simulate paste keystroke."""
        if not text:
            return

        if self.copy_to_clipboard(text):
            self.simulate_paste()


# Alternative implementation using pyperclip + pynput (more portable but heavier)
class ClipboardControllerPython:
    """
    Pure Python implementation using pyperclip and pynput.

    Install: pip install pyperclip pynput

    Note: pynput may require additional permissions on macOS.
    """

    def __init__(self):
        try:
            import pyperclip
            from pynput.keyboard import Controller, Key
            self.pyperclip = pyperclip
            self.keyboard = Controller()
            self.Key = Key
        except ImportError:
            raise RuntimeError(
                "Install dependencies: pip install pyperclip pynput"
            )

        self.system = platform.system()

    def paste_text(self, text: str):
        if not text:
            return

        try:
            self.pyperclip.copy(text)

            # Small delay to ensure clipboard is updated
            import time
            time.sleep(0.05)

            # Simulate paste
            modifier = self.Key.cmd if self.system == "Darwin" else self.Key.ctrl

            with self.keyboard.pressed(modifier):
                self.keyboard.press('v')
                self.keyboard.release('v')

        except Exception as e:
            print(f"Paste failed: {e}")


def get_clipboard_controller(prefer_python: bool = False):
    """
    Factory function to get appropriate clipboard controller.

    Args:
        prefer_python: If True, use Python-based implementation (pyperclip/pynput)
                      If False, use system tools (wl-copy, xdotool, etc.)
    """
    if prefer_python:
        return ClipboardControllerPython()
    return ClipboardController()


if __name__ == "__main__":
    # Test the clipboard controller
    controller = ClipboardController()
    print(f"Platform: {controller.system}")
    print(f"Wayland: {controller.is_wayland}")

    test_text = "Hello from MagType! "
    print(f"Testing paste with: {test_text!r}")

    input("Press Enter to test paste (focus a text field)...")
    controller.paste_text(test_text)
    print("Done!")
