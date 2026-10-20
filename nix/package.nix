{
  lib,
  python3Packages,
  qt6,
  kdePackages,
  wl-clipboard,
  xclip,
  # The glossy 3D mouse model and soft shadows need numpy; everything else works without it.
  withNumpy ? true,
  # Extra programs to put on the PATH of the installed program (for example fwupd, whose
  # fwupdmgr reads and updates the firmware of the mouse).
  extraRuntimePackages ? [ ],
}:

python3Packages.buildPythonApplication (finalAttrs: {
  pname = "mxoptions";
  version = builtins.head (
    builtins.match ".*__version__ = \"([^\"]*)\".*" (builtins.readFile ../mxoptions/__init__.py)
  );
  pyproject = true;

  src = lib.fileset.toSource {
    root = ../.;
    fileset = lib.fileset.unions [
      ../pyproject.toml
      ../README.md
      ../LICENSE
      ../mxoptions
      ../data
      ../tests
      ../docs/API.md
    ];
  };

  build-system = [ python3Packages.setuptools ];

  dependencies = [
    python3Packages.pyside6
    python3Packages.evdev
  ]
  ++ lib.optional withNumpy python3Packages.numpy;

  nativeBuildInputs = [ qt6.wrapQtAppsHook ];

  # PySide6 links against the distribution's Qt (which is what lets it see the system's
  # layer-shell QML module), so the Qt libraries, QML modules and platform plugins have to be
  # in the closure as well.
  buildInputs = [
    qt6.qtbase
    qt6.qtdeclarative
    qt6.qtmultimedia
    qt6.qtsvg
    qt6.qtwayland
    kdePackages.layer-shell-qt
  ];

  # buildPythonApplication makes a Python wrapper script, and wrapQtAppsHook wraps only ELF
  # binaries. Pass the hook's arguments to the Python wrapper instead, so Qt finds its plugins
  # and QML modules at run time.
  dontWrapQtApps = true;
  preFixup = ''
    makeWrapperArgs+=("''${qtWrapperArgs[@]}")
    makeWrapperArgs+=(
      "--prefix" "PATH" ":" "${
        lib.makeBinPath (
          [
            wl-clipboard
            xclip
          ]
          ++ extraRuntimePackages
        )
      }"
    )
  '';

  postInstall = ''
    install -Dm644 data/mxoptions.desktop "$out/share/applications/mxoptions.desktop"
    install -Dm644 mxoptions/assets/icon.svg "$out/share/icons/hicolor/scalable/apps/mxoptions.svg"
    install -Dm644 data/70-mxoptions.rules "$out/lib/udev/rules.d/70-mxoptions.rules"

    install -Dm644 README.md "$out/share/doc/mxoptions/README.md"
    install -Dm644 docs/API.md "$out/share/doc/mxoptions/API.md"
    install -Dm644 LICENSE "$out/share/doc/mxoptions/LICENSE"

    install -Dm644 /dev/null "$out/share/systemd/user/mxoptions.service"
    substitute data/mxoptions.service.in "$out/share/systemd/user/mxoptions.service" \
      --replace-fail "@BINDIR@" "$out/bin" \
      --replace-fail "@DOCDIR@" "$out/share/doc/mxoptions"
  '';

  doCheck = true;
  preCheck = ''
    export QT_QPA_PLATFORM=offscreen
    # The installed program gets these paths from the Qt wrapper; the tests run without it.
    export NIXPKGS_QT6_QML_IMPORT_PATH="${
      lib.makeSearchPath "lib/qt-6/qml" [
        qt6.qtbase
        qt6.qtdeclarative
        qt6.qtmultimedia
        qt6.qtsvg
        qt6.qtwayland
        kdePackages.layer-shell-qt
      ]
    }"
  '';
  checkPhase = ''
    runHook preCheck
    python -m unittest discover -s tests
    runHook postCheck
  '';

  pythonImportsCheck = [
    "mxoptions"
    "mxoptions.app"
    "mxoptions.api"
    "mxoptions.ui.main_window"
    "mxoptions.ui.ring"
  ];

  doInstallCheck = true;
  installCheckPhase = ''
    runHook preInstallCheck
    "$out/bin/mxoptions" --version | grep -F "MX Options ${finalAttrs.version}"
    runHook postInstallCheck
  '';

  meta = {
    description = "Settings for Logitech MX Master mice on Linux: buttons, gestures, Actions Ring, wheel and haptics";
    homepage = "https://github.com/nictonus/mxoptions";
    license = lib.licenses.mit;
    mainProgram = "mxoptions";
    platforms = lib.platforms.linux;
  };
})
