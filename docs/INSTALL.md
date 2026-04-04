# Installation Guide

Detailed installation instructions for each supported platform.

---

## CachyOS / Arch Linux

CachyOS is based on Arch Linux, so these instructions work for both.

### 1. Install System Dependencies

```bash
# Core dependencies
sudo pacman -S python python-pip portaudio libsndfile ffmpeg

# Wayland clipboard tools
sudo pacman -S wl-clipboard ydotool

# Qt6 for system tray
sudo pacman -S python-pyqt6

# Optional: For X11 instead of Wayland
# sudo pacman -S xclip xdotool
```

### 2. CUDA Support (NVIDIA GPU)

```bash
# Install CUDA toolkit
sudo pacman -S cuda cudnn

# Verify installation
nvidia-smi
nvcc --version
```

### 3. Install Python Packages

```bash
# Using pip (user installation)
pip install --user faster-whisper sounddevice soundfile numpy

# Or using pacman where available
sudo pacman -S python-numpy python-sounddevice
pip install --user faster-whisper soundfile
```

### 4. Configure ydotool

ydotool requires a running daemon and proper permissions:

```bash
# Enable and start the daemon
sudo systemctl enable --now ydotool

# Add your user to input group
sudo usermod -aG input $USER

# IMPORTANT: Log out and log back in for group changes to take effect
```

### 5. Clone and Run

```bash
git clone https://github.com/OleksandrCEO/MagType
cd magtype

# Create icons directory structure
mkdir -p icons
# Add your SVG icons: idle.svg, listening.svg, transcribing.svg

# Test run
python main.py --daemon
```

### 6. Autostart (Optional)

Create `~/.config/systemd/user/magtype.service`:

```ini
[Unit]
Description=MagType AI Dictation
After=graphical-session.target

[Service]
Type=simple
ExecStart=/usr/bin/python /path/to/magtype/main.py --daemon
Restart=on-failure
Environment=QT_QPA_PLATFORM=wayland

[Install]
WantedBy=default.target
```

```bash
systemctl --user enable --now magtype
```

---

## Ubuntu / Linux Mint / Debian

### 1. Install System Dependencies

```bash
sudo apt update
sudo apt install -y \
    python3 \
    python3-pip \
    python3-venv \
    python3-pyqt6 \
    portaudio19-dev \
    libsndfile1 \
    ffmpeg \
    wl-clipboard
```

### 2. Install ydotool

**Ubuntu 23.04+ / Debian 12+:**
```bash
sudo apt install ydotool
```

**Ubuntu 22.04 / Debian 11 (build from source):**
```bash
sudo apt install cmake scdoc libevdev-dev
git clone https://github.com/ReimuNotMoe/ydotool
cd ydotool
mkdir build && cd build
cmake ..
make
sudo make install
```

### 3. Configure ydotool Permissions

```bash
# Create udev rule for non-root access
sudo tee /etc/udev/rules.d/80-uinput.rules << 'EOF'
KERNEL=="uinput", MODE="0660", GROUP="input"
EOF

# Add user to input group
sudo usermod -aG input $USER

# Reload udev rules
sudo udevadm control --reload-rules
sudo udevadm trigger

# Log out and back in
```

Start ydotoold:
```bash
# Run manually
sudo ydotoold &

# Or create systemd service
sudo tee /etc/systemd/system/ydotool.service << 'EOF'
[Unit]
Description=ydotool daemon

[Service]
ExecStart=/usr/local/bin/ydotoold

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl enable --now ydotool
```

### 4. CUDA Support (Optional)

```bash
# Add NVIDIA CUDA repository
wget https://developer.download.nvidia.com/compute/cuda/repos/ubuntu2204/x86_64/cuda-keyring_1.1-1_all.deb
sudo dpkg -i cuda-keyring_1.1-1_all.deb
sudo apt update

# Install CUDA toolkit
sudo apt install cuda-toolkit-12-4

# Add to PATH (~/.bashrc)
export PATH=/usr/local/cuda/bin:$PATH
export LD_LIBRARY_PATH=/usr/local/cuda/lib64:$LD_LIBRARY_PATH
```

### 5. Install Python Packages

```bash
# Create virtual environment (recommended)
python3 -m venv ~/.venv/magtype
source ~/.venv/magtype/bin/activate

# Install packages
pip install faster-whisper sounddevice soundfile numpy pyqt6
```

### 6. Clone and Run

```bash
git clone https://github.com/OleksandrCEO/MagType
cd magtype

# With virtual environment
source ~/.venv/magtype/bin/activate
python main.py --daemon
```

### 7. Desktop Entry (Optional)

Create `~/.local/share/applications/magtype.desktop`:

```ini
[Desktop Entry]
Name=MagType
Comment=AI Voice Dictation
Exec=/home/USERNAME/.venv/magtype/bin/python /path/to/magtype/main.py --daemon
Icon=/path/to/magtype/icons/idle.svg
Terminal=false
Type=Application
Categories=Utility;Accessibility;
```

---

## macOS

> **Important**: macOS requires code modifications for clipboard and keyboard simulation.

### 1. Install Homebrew Dependencies

```bash
# Install Homebrew if not present
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"

# Install dependencies
brew install python portaudio ffmpeg
```

### 2. Install Python Packages

```bash
pip3 install faster-whisper sounddevice soundfile numpy pyqt6
```

### 3. Code Modifications

Create a modified `ClipboardController` for macOS:

```python
import platform
import subprocess

class ClipboardController:
    """Cross-platform clipboard controller."""

    @staticmethod
    def paste_text(text: str):
        if not text:
            return

        system = platform.system()

        try:
            if system == "Darwin":  # macOS
                # Copy to clipboard
                subprocess.run(["pbcopy"], input=text.encode(), check=True)
                # Simulate Cmd+V
                subprocess.run([
                    "osascript", "-e",
                    'tell application "System Events" to keystroke "v" using command down'
                ], check=True)

            elif system == "Linux":
                # Check if Wayland or X11
                if os.environ.get("WAYLAND_DISPLAY"):
                    subprocess.run(["wl-copy", text], check=True)
                    subprocess.run(["ydotool", "key", "29:1", "47:1", "47:0", "29:0"], check=True)
                else:
                    subprocess.run(["xclip", "-selection", "clipboard"], input=text.encode(), check=True)
                    subprocess.run(["xdotool", "key", "ctrl+v"], check=True)

        except Exception as e:
            print(f"Clipboard operation failed: {e}")
```

### 4. Grant Accessibility Permissions

macOS requires explicit permissions for keyboard simulation:

1. Open **System Preferences** → **Security & Privacy** → **Privacy**
2. Select **Accessibility** in the sidebar
3. Click the lock icon and enter your password
4. Add **Terminal** (or your terminal app) to the list
5. If using a Python app, you may need to add the Python binary

### 5. Socket Path for macOS

Unix sockets work on macOS, but the path should be in a user-accessible location:

```python
import platform

if platform.system() == "Darwin":
    SOCKET_PATH = os.path.expanduser("~/.magtype.sock")
else:
    SOCKET_PATH = "/tmp/magtype.sock"
```

### 6. Run

```bash
cd magtype
python3 main.py --daemon --device cpu  # Mac typically doesn't have CUDA
```

### 7. Hotkey with Automator

1. Open **Automator** → New Document → **Quick Action**
2. Set "Workflow receives" to **no input**
3. Add **Run Shell Script** action:
   ```bash
   /usr/local/bin/python3 /path/to/magtype/main.py --toggle
   ```
4. Save as "MagType Toggle"
5. Go to **System Preferences** → **Keyboard** → **Shortcuts** → **Services**
6. Find "MagType Toggle" and assign a shortcut

---

## Verification Checklist

After installation, verify each component:

```bash
# Check Python version
python3 --version  # Should be 3.10+

# Check faster-whisper
python3 -c "from faster_whisper import WhisperModel; print('OK')"

# Check audio
python3 -c "import sounddevice; print(sounddevice.query_devices())"

# Check clipboard (Linux Wayland)
echo "test" | wl-copy && wl-paste

# Check ydotool (Linux)
ydotool type "test"

# Check CUDA (if using GPU)
python3 -c "import torch; print(torch.cuda.is_available())"
```

---

## Troubleshooting

### "ModuleNotFoundError: No module named 'PyQt6'"

```bash
pip install pyqt6
# Or on Arch/CachyOS:
sudo pacman -S python-pyqt6
```

### "PermissionError: [Errno 13] Permission denied: '/dev/uinput'"

```bash
sudo usermod -aG input $USER
# Then log out and back in
```

### "CUDA error: no kernel image is available"

Your GPU may not be supported by the installed CUDA version. Try CPU mode:
```bash
python main.py --daemon --device cpu
```

### macOS: "osascript not allowed assistive access"

Grant accessibility permissions as described in section 4 of macOS installation.
