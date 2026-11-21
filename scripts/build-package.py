#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Create an exact local source snapshot and build its native Arch package."""
import hashlib
from pathlib import Path
import subprocess
import tarfile

root = Path(__file__).resolve().parents[1]
work = root / 'build/package'; work.mkdir(parents=True, exist_ok=True)
name = 'omarchy-update-compat-0.2.0'
archive = work / (name + '.tar.gz')
with tarfile.open(archive, 'w:gz') as tar:
    for entry in ['bin', 'omarchy_compat', 'native', 'builders', 'policies', 'wrappers', 'libexec', 'README.md', 'ACCEPTANCE.md', 'LICENSE']:
        tar.add(root / entry, arcname=f'{name}/{entry}',
                filter=lambda item: None if '__pycache__' in item.name or item.name.endswith('.pyc') else item)
checksum = hashlib.sha256(archive.read_bytes()).hexdigest()
recipe = (root / 'packaging/PKGBUILD').read_text().replace("sha256sums=('SKIP')", f"sha256sums=('{checksum}')")
(work / 'PKGBUILD').write_text(recipe)
subprocess.run(['makepkg', '--nodeps', '--force', '--noconfirm'], cwd=work, check=True)
print('Native package:', *work.glob('*.pkg.tar.zst'))
