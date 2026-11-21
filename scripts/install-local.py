#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Carlos Diaz
# SPDX-License-Identifier: GPL-3.0-or-later
"""Install the unprivileged POC without changing Omarchy's updater."""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--cache-from', type=Path, help='Import an existing verified build cache')
args = parser.parse_args()
source = Path(__file__).resolve().parents[1]
home = Path.home()
destination = home / '.local/lib/omarchy-compat-poc'
entry = home / '.local/bin/omarchy-update-compat'
marker = destination / '.omarchy-compat-poc'
if destination.exists() and not marker.is_file():
    raise SystemExit(f'Refusing to overwrite an unrelated directory: {destination}')
if os.path.lexists(entry) and (not entry.is_symlink() or entry.resolve() != destination / 'bin/omarchy-update-compat'):
    raise SystemExit(f'Refusing to replace an existing command: {entry}')
destination.mkdir(parents=True, exist_ok=True)
for name in ['bin', 'omarchy_compat', 'native', 'builders', 'examples', 'integration', 'validation']:
    shutil.copytree(source / name, destination / name, dirs_exist_ok=True,
                    ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
for name in ['README.md', 'CONTRIBUTING-PLAN.md', 'LICENSE']:
    shutil.copyfile(source / name, destination / name)
marker.write_text('Unprivileged Omarchy update compatibility POC\n')
entry.parent.mkdir(parents=True, exist_ok=True)
if not entry.is_symlink():
    entry.symlink_to(destination / 'bin/omarchy-update-compat')
if args.cache_from:
    old_cache = args.cache_from.resolve()
    new_cache = home / '.cache/omarchy/update-compat'
    if new_cache.exists():
        raise SystemExit(f'Tool installed; refusing to overwrite existing cache: {new_cache}')
    new_cache.mkdir(parents=True)
    subprocess.run(['cp', '-a', '--reflink=auto', str(old_cache) + '/.', new_cache], check=True)
    for index in (new_cache / 'source-index').glob('*.json'):
        record = json.loads(index.read_text())
        for candidate in record['candidates']:
            relative = Path(candidate['path']).relative_to(old_cache)
            candidate['path'] = str(new_cache / relative)
        index.write_text(json.dumps(record, indent=2) + '\n')
print(f'Installed {entry} -> {destination}')
print('Next: omarchy-update-compat track gaze')
