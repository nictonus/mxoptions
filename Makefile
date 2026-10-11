# MX Options: build, test, install.
#
#   make                  compile everything and check the translations
#   make test             run the unit tests
#   make install-user     install for the current user under ~/.local (no root needed)
#   make enable           start the service now and at every login
#   sudo make install     install for every user under /usr/local
#   sudo make udev        install the device access rules (once; see README, "Permissions")
#   make uninstall-user   remove the per-user installation
#   sudo make uninstall   remove the system-wide installation
#   make dist             build the files of a release into dist/ (after its tag is on GitHub)
#
# Packagers: make DESTDIR=... PREFIX=/usr udevdir=/usr/lib/udev/rules.d install

PREFIX ?= /usr/local
DESTDIR ?=
PYTHON ?= python3

bindir = $(PREFIX)/bin
libdir = $(PREFIX)/lib/mxoptions
datadir = $(PREFIX)/share
docdir = $(datadir)/doc/mxoptions
licensedir = $(datadir)/licenses/mxoptions
unitdir = $(PREFIX)/lib/systemd/user
udevdir = /etc/udev/rules.d
# empty: leave the udev rules out (a per-user installation cannot place them)
UDEV = yes

user_prefix = $(HOME)/.local
user_unitdir = $(or $(XDG_CONFIG_HOME),$(HOME)/.config)/systemd/user
python_path = $(shell command -v $(PYTHON))
substitute = sed -e 's|@BINDIR@|$(bindir)|g' -e 's|@LIBDIR@|$(libdir)|g' -e 's|@DOCDIR@|$(docdir)|g' \
	-e 's|@PYTHON@|$(python_path)|g'

.PHONY: all build test check wheel dist run screenshots install install-user uninstall uninstall-user enable disable udev clean

all: build

build:
	$(PYTHON) -m compileall -q mxoptions
	$(PYTHON) tools/check_i18n.py

test:
	$(PYTHON) -m unittest discover -s tests

check: build test

wheel:
	$(PYTHON) -m build --wheel --no-isolation --outdir dist

# What a release on GitHub carries: the Arch package (built by packaging/aur/PKGBUILD from the
# tagged source archive, so the tag has to be pushed first), the wheel, and their checksums.
dist:
	rm -rf dist build
	mkdir -p dist build/makepkg
	$(PYTHON) -m build --wheel --no-isolation --outdir dist
	cd packaging/aur && PKGDEST="$(CURDIR)/dist" BUILDDIR="$(CURDIR)/build/makepkg" SRCDEST="$(CURDIR)/build/makepkg" makepkg -f
	rm -rf build *.egg-info
	cd dist && sha256sum * > SHA256SUMS

run:
	$(PYTHON) main.py

screenshots:
	$(PYTHON) tools/screenshots.py docs/screenshots

install: build
	install -d "$(DESTDIR)$(libdir)" "$(DESTDIR)$(bindir)" "$(DESTDIR)$(unitdir)"
	rm -rf "$(DESTDIR)$(libdir)/mxoptions"
	cp -r mxoptions "$(DESTDIR)$(libdir)/mxoptions"
	find "$(DESTDIR)$(libdir)/mxoptions" -name __pycache__ -prune -exec rm -rf {} +
	$(PYTHON) -m compileall -q -d "$(libdir)/mxoptions" "$(DESTDIR)$(libdir)/mxoptions"
	$(substitute) data/mxoptions.in > "$(DESTDIR)$(bindir)/mxoptions"
	chmod 755 "$(DESTDIR)$(bindir)/mxoptions"
	$(substitute) data/mxoptions.service.in > "$(DESTDIR)$(unitdir)/mxoptions.service"
	install -d "$(DESTDIR)$(datadir)/applications"
	sed -e 's|^Exec=mxoptions|Exec=$(bindir)/mxoptions|' data/mxoptions.desktop > "$(DESTDIR)$(datadir)/applications/mxoptions.desktop"
	install -Dm644 mxoptions/assets/icon.svg "$(DESTDIR)$(datadir)/icons/hicolor/scalable/apps/mxoptions.svg"
	install -Dm644 README.md "$(DESTDIR)$(docdir)/README.md"
	install -Dm644 docs/API.md "$(DESTDIR)$(docdir)/API.md"
	install -Dm644 LICENSE "$(DESTDIR)$(licensedir)/LICENSE"
ifeq ($(UDEV),yes)
	install -Dm644 data/70-mxoptions.rules "$(DESTDIR)$(udevdir)/70-mxoptions.rules"
endif

uninstall:
	rm -rf "$(DESTDIR)$(libdir)" "$(DESTDIR)$(docdir)" "$(DESTDIR)$(licensedir)"
	rm -f "$(DESTDIR)$(bindir)/mxoptions" "$(DESTDIR)$(unitdir)/mxoptions.service" \
		"$(DESTDIR)$(datadir)/applications/mxoptions.desktop" \
		"$(DESTDIR)$(datadir)/icons/hicolor/scalable/apps/mxoptions.svg"
ifeq ($(UDEV),yes)
	rm -f "$(DESTDIR)$(udevdir)/70-mxoptions.rules"
endif

install-user:
	$(MAKE) install PREFIX="$(user_prefix)" unitdir="$(user_unitdir)" UDEV=
	-systemctl --user daemon-reload
	@echo
	@echo "Installed under $(user_prefix). Next:"
	@echo "  make enable       start it now and at every login"
	@echo "  sudo make udev    only if the program cannot reach the mouse (see README, Permissions)"

uninstall-user: disable
	$(MAKE) uninstall PREFIX="$(user_prefix)" unitdir="$(user_unitdir)" UDEV=
	-systemctl --user daemon-reload

enable:
	systemctl --user daemon-reload
	systemctl --user enable --now mxoptions.service

disable:
	-systemctl --user disable --now mxoptions.service

udev:
	install -Dm644 data/70-mxoptions.rules "$(DESTDIR)$(udevdir)/70-mxoptions.rules"
	-udevadm control --reload-rules
	-udevadm trigger --subsystem-match=hidraw
	-udevadm trigger --sysname-match=uinput

clean:
	rm -rf build dist *.egg-info packaging/aur/pkg packaging/aur/src
	find . -name __pycache__ -prune -exec rm -rf {} +
