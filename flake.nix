{
  description = "Settings for Logitech MX Master mice on Linux: buttons, gestures, Actions Ring, wheel and haptics";

  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";

  outputs =
    { self, nixpkgs }:
    let
      systems = [
        "x86_64-linux"
        "aarch64-linux"
      ];
      forAllSystems = f: nixpkgs.lib.genAttrs systems (system: f nixpkgs.legacyPackages.${system});
    in
    {
      packages = forAllSystems (
        pkgs:
        let
          mxoptions = pkgs.callPackage ./nix/package.nix { };
        in
        {
          inherit mxoptions;
          default = mxoptions;
        }
      );

      overlays.default = final: prev: {
        mxoptions = final.callPackage ./nix/package.nix { };
      };

      nixosModules.default = import ./nix/module.nix { inherit self; };
      nixosModules.mxoptions = self.nixosModules.default;

      devShells = forAllSystems (pkgs: {
        default = pkgs.mkShell {
          packages = with pkgs; [
            (python3.withPackages (
              ps: with ps; [
                pyside6
                evdev
                numpy
              ]
            ))
            qt6.qtbase
            qt6.qtdeclarative
            qt6.qtmultimedia
            qt6.qtsvg
            qt6.qtwayland
            kdePackages.layer-shell-qt
            wl-clipboard
            xclip
          ];
          # The ring tests load QML modules from the Qt packages above. An installed copy gets
          # this from its wrapper; a development shell needs it set itself.
          NIXPKGS_QT6_QML_IMPORT_PATH = pkgs.lib.makeSearchPath "lib/qt-6/qml" (
            with pkgs;
            [
              qt6.qtbase
              qt6.qtdeclarative
              qt6.qtmultimedia
              qt6.qtsvg
              qt6.qtwayland
              kdePackages.layer-shell-qt
            ]
          );
        };
      });

      formatter = forAllSystems (pkgs: pkgs.nixfmt);

      checks = forAllSystems (pkgs: {
        inherit (self.packages.${pkgs.stdenv.hostPlatform.system}) mxoptions;
      });
    };
}
