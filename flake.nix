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
        config = {
          allowUnfree = true;
          # This global flag tells Nix to build/fetch packages with CUDA support
          cudaSupport = true;
        };
      };

      # Essential runtime libraries
      runtimeLibs = with pkgs; [
        stdenv.cc.cc.lib
        zlib
        glib
        libGL
        libxkbcommon
        fontconfig
        freetype
        wayland
        qt6.qtbase
        qt6.qtsvg
        qt6.qtwayland
        portaudio
        libsndfile
        # Specific CUDA packages required at runtime
        cudaPackages.cudatoolkit
        cudaPackages.cudnn
        cudaPackages.libcublas
      ];

      # Python environment.
      # Since we set cudaSupport = true above, ps.faster-whisper
      # will depend on the CUDA-enabled version of ctranslate2.
      pythonEnv = pkgs.python3.withPackages (ps: with ps; [
        pyqt6
        numpy
        sounddevice
        soundfile
        faster-whisper
      ]);

      binPath = with pkgs; [
        wl-clipboard
        ydotool
        libnotify
        ffmpeg
      ];

    in
    {
      packages.${system}.default = pkgs.stdenv.mkDerivation {
        pname = "magtype";
        version = "1.0.0";
        src = ./.;

        nativeBuildInputs = [ pkgs.makeWrapper ];

        installPhase = ''
          mkdir -p $out/bin $out/share/magtype $out/share/icons/magtype

          # Deploy source code and assets
          cp main.py $out/share/magtype/
          if [ -d "icons" ]; then
            cp -r icons/* $out/share/icons/magtype/
          fi

          # Create a wrapper to handle environment variables and library paths
          makeWrapper ${pythonEnv}/bin/python $out/bin/magtype \
            --add-flags "$out/share/magtype/main.py" \
            --prefix LD_LIBRARY_PATH : "${pkgs.lib.makeLibraryPath runtimeLibs}:/run/opengl-driver/lib" \
            --set QT_QPA_PLATFORM "wayland;xcb" \
            --set QT_PLUGIN_PATH "${pkgs.qt6.qtbase}/${pkgs.qt6.qtbase.qtPluginPrefix}" \
            --set NIXOS_OZONE_WL "1" \
            --prefix PATH : "${pkgs.lib.makeBinPath binPath}" \
            --set MAGTYPE_ICONS_PATH "$out/share/icons/magtype"
        '';
      };

      devShells.${system}.default = pkgs.mkShell {
        buildInputs = [
          pythonEnv
        ] ++ binPath;

        # Ensure CUDA and OpenGL libraries are visible during development
        LD_LIBRARY_PATH = "${pkgs.lib.makeLibraryPath runtimeLibs}:/run/opengl-driver/lib";

        shellHook = ''
          export QT_QPA_PLATFORM=wayland
          export MAGTYPE_ICONS_PATH="./icons"
          echo "🎙️ MagType (CUDA) dev environment loaded"
        '';
      };

      nixosModules.default = { config, lib, pkgs, ... }: {
        options.services.magtype.enable = lib.mkEnableOption "MagType AI Dictation";
        config = lib.mkIf config.services.magtype.enable {
          environment.systemPackages = [ self.packages.${pkgs.system}.default ];
        };
      };

    };
}