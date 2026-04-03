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

      # Всі бібліотеки, які ми вивели досвідним шляхом
      runtimeLibs = with pkgs; [
        stdenv.cc.cc.lib
        zlib
        glib
        libGL
        libxkbcommon
        fontconfig
        freetype
        wayland
        # Qt6 модулі
        qt6.qtbase
        qt6.qtsvg
        qt6.qtwayland
        # Audio
        portaudio
        libsndfile
        # CUDA
        cudaPackages.cudatoolkit
        cudaPackages.cudnn
        cudaPackages.libcublas
      ];

      # Формуємо Python з усіма потрібними пакунками з Nixpkgs
      pythonEnv = pkgs.python3.withPackages (ps: with ps; [
        pyqt6
        numpy
        sounddevice
        soundfile
        faster-whisper
      ]);

    in
    {
      # 1. СЕКЦІЯ ПАКУНКА (для встановлення в систему)
      packages.${system}.default = pkgs.stdenv.mkDerivation {
        pname = "magtype";
        version = "1.0.0";
        src = ./.;

        nativeBuildInputs = [ pkgs.makeWrapper ];

        installPhase = ''
          mkdir -p $out/bin $out/share/magtype

          # Копіюємо скрипт та іконки в share
          cp main.py $out/share/magtype/
          cp -r icons $out/share/magtype/ || mkdir -p $out/share/magtype/icons

          # Створюємо "обгортку" (wrapper), яка замінює прямий виклик python.
          # Вона автоматично встановлює всі змінні оточення при запуску.
          makeWrapper ${pythonEnv}/bin/python $out/bin/magtype \
            --add-flags "$out/share/magtype/main.py" \
            --prefix LD_LIBRARY_PATH : "${pkgs.lib.makeLibraryPath runtimeLibs}:/run/opengl-driver/lib" \
            --set QT_QPA_PLATFORM wayland \
            --set QT_PLUGIN_PATH "${pkgs.qt6.qtbase}/${pkgs.qt6.qtbase.qtPluginPrefix}:${pkgs.qt6.qtsvg}/${pkgs.qt6.qtbase.qtPluginPrefix}" \
            --prefix PATH : "${pkgs.lib.makeBinPath [ pkgs.wl-clipboard pkgs.ydotool pkgs.libnotify pkgs.ffmpeg ]}"
        '';
      };

      # 2. СЕКЦІЯ ДЛЯ РОЗРОБКИ (лишаємо як була)
      devShells.${system}.default = pkgs.mkShell {
        buildInputs = [
          pythonEnv
          pkgs.portaudio
          pkgs.wl-clipboard
          pkgs.ydotool
          pkgs.libnotify
          pkgs.ffmpeg
        ];

        LD_LIBRARY_PATH = "${pkgs.lib.makeLibraryPath runtimeLibs}:/run/opengl-driver/lib";

        shellHook = ''
          export QT_PLUGIN_PATH="${pkgs.qt6.qtbase}/${pkgs.qt6.qtbase.qtPluginPrefix}:${pkgs.qt6.qtsvg}/${pkgs.qt6.qtbase.qtPluginPrefix}"
          export QT_QPA_PLATFORM=wayland
          echo "🎙️ MagType CUDA Development Environment Loaded!"
        '';
      };
    };
}