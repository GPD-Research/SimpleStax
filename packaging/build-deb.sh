#!/usr/bin/env bash
# Freezes SimpleStax with PyInstaller and wraps it in dist-deb/simplestax_<version>_amd64.deb.
# Freezing keeps the .deb independent of the system's Python and Qt; build on the oldest Ubuntu
# to be supported (CI uses 22.04), since the bundle links against that glibc.
set -euo pipefail
cd "$(dirname "$0")/.."

version="$(python3 -c 'import sys; sys.path.insert(0, "src"); import simplestax; print(simplestax.__version__)')"
pkg="simplestax_${version}_amd64"
stage="build/deb/${pkg}"

rm -rf build/pyinstaller dist/simplestax "${stage}"
pyinstaller --noconfirm --clean --windowed --name simplestax \
  --workpath build/pyinstaller --distpath dist --specpath build \
  --paths src --collect-submodules simplestax \
  --exclude-module PySide6.QtWebEngineCore --exclude-module PySide6.Qt3DCore \
  src/simplestax/__main__.py

install -d "${stage}/DEBIAN" "${stage}/opt/simplestax" "${stage}/usr/bin" \
  "${stage}/usr/share/applications" "${stage}/usr/share/icons/hicolor/scalable/apps" \
  "${stage}/usr/share/doc/simplestax"
cp -a dist/simplestax/. "${stage}/opt/simplestax/"
ln -s /opt/simplestax/simplestax "${stage}/usr/bin/simplestax"
install -m 644 packaging/simplestax.desktop "${stage}/usr/share/applications/"
install -m 644 packaging/simplestax.svg "${stage}/usr/share/icons/hicolor/scalable/apps/"
install -m 644 LICENSE "${stage}/usr/share/doc/simplestax/copyright"

size="$(du -sk "${stage}" | cut -f1)"
cat > "${stage}/DEBIAN/control" <<CONTROL
Package: simplestax
Version: ${version}
Architecture: amd64
Maintainer: GPD Research <gregorydearth@gmail.com>
Installed-Size: ${size}
Depends: libc6 (>= 2.35), libgl1, libegl1, libxkbcommon-x11-0, libxcb-cursor0, libfontconfig1, python3, python3-venv
Section: graphics
Priority: optional
Homepage: https://github.com/GPD-Research/SimpleStax
Description: Guided calibration and stacking for astrophotography
 Plan a night's shoot, import and validate calibration and light frames, and stack
 them with a workflow chosen for the subject: planetary, nebulae, galaxies, star
 fields or the Milky Way.
CONTROL

mkdir -p dist-deb
dpkg-deb --root-owner-group --build "${stage}" "dist-deb/${pkg}.deb"
echo "dist-deb/${pkg}.deb"
