# MX Options for Fedora and other RPM distributions.
#
#   rpmbuild -ba mxoptions.spec      after putting the source archive of the tag into ~/rpmbuild/SOURCES
#
# The archive is the one GitHub makes for a tag:  https://github.com/nictonus/mxoptions/tags

%global tag     v0.10.0
%global srcdir  mxoptions-0.10.0

Name:           mxoptions
Version:        0.10.0
Release:        1%{?dist}
Summary:        Settings for Logitech MX Master mice

License:        MIT
URL:            https://github.com/nictonus/mxoptions
Source0:        %{url}/archive/refs/tags/%{tag}.tar.gz#/%{srcdir}.tar.gz
BuildArch:      noarch

BuildRequires:  make
BuildRequires:  python3
BuildRequires:  systemd-rpm-macros

Requires:       python3 >= 3.12
Requires:       python3-pyside6
Requires:       python3-evdev
Requires:       qt6-qtdeclarative
Requires:       qt6-qtmultimedia
Requires:       qt6-qtsvg
Requires:       layer-shell-qt
Recommends:     python3-numpy
Recommends:     fwupd
Recommends:     wl-clipboard

%description
Buttons, gestures, the Actions Ring, the wheels, haptics and Easy-Switch of
Logitech MX mice, with a tray icon, a background service and a D-Bus interface
for other programs. Flow moves the mouse between two computers.

Unofficial: not affiliated with or endorsed by Logitech.

%prep
%autosetup -n %{srcdir}

%build
make build

%install
make DESTDIR=%{buildroot} PREFIX=%{_prefix} udevdir=%{_udevrulesdir} install
# the licence and the documentation are taken from the source tree by the macros below
rm -rf %{buildroot}%{_datadir}/doc/mxoptions %{buildroot}%{_datadir}/licenses/mxoptions

%post
%udev_rules_update
udevadm trigger --subsystem-match=hidraw >/dev/null 2>&1 || :
udevadm trigger --sysname-match=uinput >/dev/null 2>&1 || :

%postun
%udev_rules_update

%files
%license LICENSE
%doc README.md docs/API.md
%{_bindir}/mxoptions
%{_prefix}/lib/mxoptions/
%{_userunitdir}/mxoptions.service
%{_datadir}/applications/mxoptions.desktop
%{_datadir}/icons/hicolor/scalable/apps/mxoptions.svg
%{_udevrulesdir}/70-mxoptions.rules

%changelog
* Wed Oct 14 2026 nictonus <nictonus@users.noreply.github.com> - 0.10.0-1
- 0.10.0: Flow, desktops other than KDE Plasma, Windows

* Wed Oct 14 2026 nictonus <nictonus@users.noreply.github.com> - 0.10.0~b3-1
- Works on desktops other than KDE Plasma; first build for Windows

* Mon Oct 13 2026 nictonus <nictonus@users.noreply.github.com> - 0.10.0~b2-1
- Second beta of 0.10.0: Flow for three computers, game rumble, shapes, tray icon styles
