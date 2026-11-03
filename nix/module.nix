{ self }:
{
  config,
  lib,
  pkgs,
  ...
}:

let
  cfg = config.services.mxoptions;
  package =
    if cfg.fwupd then cfg.package.override { extraRuntimePackages = [ pkgs.fwupd ]; } else cfg.package;
in
{
  options.services.mxoptions = {
    enable = lib.mkEnableOption "MX Options, a settings program and background service for Logitech MX mice";

    package = lib.mkOption {
      type = lib.types.package;
      default = self.packages.${pkgs.stdenv.hostPlatform.system}.mxoptions;
      defaultText = lib.literalExpression "self.packages.\${pkgs.stdenv.hostPlatform.system}.mxoptions";
      description = ''
        The mxoptions package to install. It brings the udev rules that let the logged-in
        user reach the mouse and the virtual input device.
      '';
    };

    fwupd = lib.mkOption {
      type = lib.types.bool;
      default = false;
      description = ''
        Make {command}`fwupdmgr` available to MX Options, which uses it to show the
        firmware versions of the mouse and its receiver and to check for updates.
        This does not enable the fwupd service; set {option}`services.fwupd.enable`
        for that.
      '';
    };
  };

  config = lib.mkIf cfg.enable {
    environment.systemPackages = [ package ];

    # $out/lib/udev/rules.d/70-mxoptions.rules: access to Logitech HID++ devices and to uinput.
    services.udev.packages = [ cfg.package ];
    boot.kernelModules = [ "uinput" ];

    # The same unit as data/mxoptions.service.in, but defined here so that it works without
    # systemctl --user enable and follows the graphical session of every user.
    systemd.user.services.mxoptions = {
      description = "MX Options: buttons, gestures, wheel and haptics of Logitech MX mice";
      documentation = [ "file://${package}/share/doc/mxoptions/README.md" ];
      partOf = [ "graphical-session.target" ];
      after = [ "graphical-session.target" ];
      wantedBy = [ "graphical-session.target" ];
      # Without this NixOS sets a PATH of coreutils and the like, and commands assigned to buttons
      # would not find the user's programs. The user manager's own PATH has them.
      environment.PATH = lib.mkForce null;
      serviceConfig = {
        ExecStart = "${package}/bin/mxoptions --background";
        Restart = "on-failure";
        RestartSec = 3;
        TimeoutStopSec = 8;
      };
    };
  };
}
