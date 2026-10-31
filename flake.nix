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
        ];

        shellHook = ''
        echo "-- Entered DevShell --"
        '';
      };
    }) inputs.nixpkgs.legacyPackages;
  };
}
