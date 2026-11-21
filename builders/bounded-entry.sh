#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
set -euo pipefail
if [[ $(cat /sys/fs/cgroup/memory.max) != "4294967296" || $(cat /sys/fs/cgroup/pids.max) != "512" || $(cat /sys/fs/cgroup/cpu.max) != "200000 100000" ]]; then
  echo 'Build resource controls are unavailable; refusing an unbounded build.' >&2
  exit 1
fi
echo 'Build limits verified: memory 4 GiB, CPU 2, PIDs 512.' >&2
export HOME=/tmp/build-home CARGO_HOME=/work/deps/cargo
export ORT_CACHE_DIR=/work/deps/ort
mkdir -p "$HOME" /work/source /work/deps
tar --no-same-owner -xf /input/source.tar -C /work/source
cd /work/source
if [[ $1 == "fetch" ]]; then
  cargo fetch --locked >&2
  bash scripts/fetch-ort.sh 1.32.0 x64 "$ORT_CACHE_DIR/1.32.0/x86_64/onnxruntime.tgz" >&2
  tar -C /work/deps -cf - cargo ort
elif [[ $1 == "compile" ]]; then
  tar --no-same-owner -xf /input/deps.tar -C /work/deps
  export CARGO_NET_OFFLINE=true CARGO_BUILD_JOBS=1
  just package archlinux >&2
  tar -C dist/packages -cf - \
    "gaze-$VERSION-1-x86_64.pkg.tar.zst" \
    "gaze-gui-$VERSION-1-x86_64.pkg.tar.zst" \
    "gaze-omarchy-$VERSION-1-x86_64.pkg.tar.zst"
else
  exit 2
fi
