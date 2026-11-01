{
  description = "Guessless DevShell";

  inputs = {
    nixpkgs.url = "https://channels.nixos.org/nixpkgs-unstable/nixexprs.tar.zst";
  };

  outputs = inputs: {
    devShells = builtins.mapAttrs (system: pkgs: {
      default = pkgs.mkShell {
        packages = with pkgs; [
          python314
          uv
          python314Packages.tkinter
          fish
          # for pygame
          wayland
          SDL2
          libxkbcommon
          libdecor
          libglvnd
        ];

        shellHook = ''
          export LD_LIBRARY_PATH=$LD_LIBRARY_PATH:${pkgs.lib.makeLibraryPath (with pkgs; [ wayland libdecor libxkbcommon libglvnd ])}
          echo "-- Entered DevShell --"
        '';
      };
    }) inputs.nixpkgs.legacyPackages;
  };
}
