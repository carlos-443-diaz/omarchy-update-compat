#!/bin/bash
# SPDX-FileCopyrightText: 2026 Carlos Diaz
# SPDX-License-Identifier: GPL-3.0-or-later
# Run only INSIDE a disposable container, with a staged bundle as $1 and
# the patched Omarchy checkout mounted at /omarchy-poc.
set -euo pipefail
[[ -e /run/.containerenv || -e /.dockerenv ]]
cd "$(dirname "$0")/.."
export PATH="$PWD/bin:$PATH"
export OMARCHY_COMPAT_CONFIG="$PWD/examples/compat.toml"
export OMARCHY_COMPAT_CACHE="$PWD/.cache"
export OMARCHY_COMPAT_STATE=/tmp/omarchy-compat-smoke

pacman -Syu --noconfirm --needed jq
mkdir -p /etc/gaze
touch /etc/gaze/pam.optout
python - "$1" > /tmp/compat-bootstrap <<'PY'
import json, sys
from pathlib import Path
p = json.load(open(sys.argv[1]))
for item in p['packages']:
    print(Path(p['repository']) / item['filename'])
PY
mapfile -t packages </tmp/compat-bootstrap
pacman -U --noconfirm "${packages[@]}"

omarchy-update-compat --offline stage --json >/tmp/compat-stage.json
python - <<'PY'
import json
p = json.load(open('/tmp/compat-stage.json'))
assert p['status'] == 'ready', p
assert p['applications'][0]['action'] == 'cached-source', p
assert p['native_final']['compatible'], p
print('PASS: native target preparation reuses the source build')
PY
OMARCHY_COMPAT_BUNDLE=$(python -c 'import json; print(json.load(open("/tmp/compat-stage.json"))["bundle"])')
export OMARCHY_COMPAT_BUNDLE
omarchy-update-compat verify "$OMARCHY_COMPAT_BUNDLE"
/omarchy-poc/bin/omarchy-update-pacman -Syu --noconfirm
pacman -Dk
pacman -Q gaze-bin gaze-gui-bin gaze-omarchy-bin opencv
gaze --version
gaze-omarchy --help

for binary in /usr/bin/gazed /usr/bin/gaze /usr/bin/gaze-gui \
  /usr/lib/security/pam_gaze.so /usr/lib/security/pam_gaze_grosshack.so \
  /usr/lib/gaze/libonnxruntime.so; do
  ldd -r "$binary" >/tmp/compat-loader.log 2>&1
  if grep -E 'not found|undefined symbol' /tmp/compat-loader.log; then
    echo "FAIL: loader resolution for $binary" >&2
    exit 1
  fi
  echo "PASS: loader resolution for $binary"
done
status=0
gazed --smoke-test-invalid-option >/tmp/compat-daemon.log 2>&1 || status=$?
[[ $status == "1" ]]
grep -F 'unknown argument; supported maintenance option:' /tmp/compat-daemon.log
systemd-analyze verify /usr/lib/systemd/system/gazed.service
echo 'PASS: real pacman transaction through patched Omarchy helper in disposable container'
echo 'Camera, graphical lock, boot and physical authentication are outside this smoke test.'
