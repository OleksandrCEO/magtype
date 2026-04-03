{
  description = "MagType - Local AI Dictation Environment";

  inputs = {
    # Using the stable 25.11 branch to match the system OS
    nixpkgs.url = "github:nixos/nixpkgs/nixos-25.11";
  };

  outputs = { self, nixpkgs }:
    let
      system = "x86_64-linux";
      pkgs = nixpkgs.legacyPackages.${system};

      # Define the Python environment with required packages
      pythonEnv = pkgs.python3.withPackages (ps: with ps; [
        numpy
        sounddevice
        soundfile
        faster-whisper
        pystray        # System tray icon
        pillow         # Image drawing for the tray icon
      ]);
    in
    {
      devShells.${system}.default = pkgs.mkShell {
        # System-level dependencies required for the script to run
        buildInputs = with pkgs; [
          pythonEnv
          portaudio
          wl-clipboard   # For clipboard operations
          ydotool        # Wayland automation tool (for pressing Ctrl+V later)
          libnotify
          ffmpeg

          gobject-introspection # Дозволяє Python модулю 'gi' бачити системні бібліотеки
          gtk3                  # Сама графічна бібліотека
          libappindicator-gtk3  # Спеціальна бібліотека для іконок у системному треї
        ];

        shellHook = ''
          echo "🎙️ MagType development environment loaded!"
        '';
      };
    };
}