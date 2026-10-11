# Arch Linux package, built from this checkout:
#
#   makepkg -si
#
# Remove a per-user installation first (make uninstall-user), or the two will both be on the PATH.

pkgname=mxoptions
pkgver=0.9.0
pkgrel=1
pkgdesc="Settings for Logitech MX Master mice: buttons, gestures, Actions Ring, wheel and haptics"
arch=('any')
license=('MIT')
depends=('python' 'pyside6' 'python-evdev' 'qt6-declarative' 'qt6-multimedia' 'layer-shell-qt')
makedepends=('make')
optdepends=('python-numpy: glossy 3D mouse model and soft shadows')
install=mxoptions.install
source=()

pkgver() {
  python -c "import sys; sys.path.insert(0, '$startdir'); import mxoptions; print(mxoptions.__version__)"
}

package() {
  make -C "$startdir" DESTDIR="$pkgdir" PREFIX=/usr udevdir=/usr/lib/udev/rules.d install
}
