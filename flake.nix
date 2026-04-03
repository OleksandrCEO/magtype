{
  description = "MagType - Local AI Dictation Environment (CUDA)";

  inputs = {
    nixpkgs.url = "github:nixos/nixpkgs/nixos-25.11";
  };

  outputs = { self, nixpkgs }:
    let
      system = "x86_64-linux";
      pkgs = import nixpkgs {
        inherit system;
        config.allowUnfree = true;
      };

      runtimeLibs = with pkgs; [
        stdenv.cc.cc.lib
        zlib
        glib

        # Audio
        portaudio
        libsndfile

        # CUDA
        cudaPackages.cudatoolkit
        cudaPackages.cudnn
        cudaPackages.libcublas

        # Tray & UI (Crucial for KDE/Wayland)
        gtk3
        libappindicator-gtk3
        libdbusmenu-gtk3
        gdk-pixbuf
      ];
    in
    {
      devShells.${system}.default = pkgs.mkShell {
        buildInputs = with pkgs; [
          python3
          portaudio
          wl-clipboard
          ydotool
          libnotify
          ffmpeg
        ];

        LD_LIBRARY_PATH = "${pkgs.lib.makeLibraryPath runtimeLibs}:/run/opengl-driver/lib";

        shellHook = ''
          if [ ! -d ".venv" ]; then
            python3 -m venv .venv
          fi
          source .venv/bin/activate
          echo "🎙️ MagType CUDA Environment Loaded!"
        '';
      };
    };
}