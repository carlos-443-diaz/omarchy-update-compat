# SPDX-FileCopyrightText: 2026 Carlos Diaz
# SPDX-License-Identifier: GPL-3.0-or-later
"""Validate a transaction bundle and expose an isolated integration contract."""
import json
from pathlib import Path
import re
import shutil
import tempfile

from . import gaze, native


def verify(bundle_path, config_path='/etc/pacman.conf', source_db='/var/lib/pacman', cache=None):
    bundle_path = Path(bundle_path).resolve()
    bundle = json.loads(bundle_path.read_text())
    if bundle.get('schema_version') != 1:
        raise RuntimeError('Unsupported transaction bundle schema.')
    allowed = set(gaze.NAMES.values())
    targets = bundle.get('targets', [])
    if set(targets) != allowed or len(targets) != len(allowed):
        raise RuntimeError('Bundle contains targets outside the managed Gaze group.')
    if len(bundle.get('packages', [])) != len(allowed):
        raise RuntimeError('Bundle must contain exactly one archive per managed package.')
    if native.local_fingerprint(source_db) != bundle['installed_fingerprint']:
        raise RuntimeError('Installed package state changed after staging; replan.')
    settings = native.effective_config(config_path)
    target = bundle['target']
    if (settings['architecture'] != target['architecture'] or settings['servers'] != target['servers']
            or settings['repositories'] != target.get('repository_order', list(target['repositories']))
            or settings['ignore'] != target.get('ignore', [])
            or settings['ignore_groups'] != target.get('ignore_groups', [])):
        raise RuntimeError('Target repository configuration changed after staging; replan.')
    repo = Path(bundle['repository'])
    if repo.resolve() != bundle_path.parent / 'repository':
        raise RuntimeError('Bundle repository is outside its staging directory.')
    archives, seen = [], set()
    for item in bundle['packages']:
        filename = item['filename']
        if Path(filename).name != filename or filename in ('', '.', '..'):
            raise RuntimeError('Invalid archive filename in bundle.')
        archive = repo / filename
        if archive.is_symlink() or not archive.is_file() or native.digest(archive) != item['sha256']:
            raise RuntimeError('Staged archive was replaced or modified.')
        metadata = gaze.metadata(archive)
        if metadata['pkgname'] != [item['package']] or metadata['pkgver'] != [item['version']]:
            raise RuntimeError('Archive identity differs from the staged manifest.')
        if item['package'] not in allowed or item['package'] in seen:
            raise RuntimeError('Unexpected or repeated archive identity.')
        seen.add(item['package']); archives.append(archive)
    with tempfile.TemporaryDirectory(prefix='omarchy-compat-verify-') as temp:
        # Fresh plans may legitimately be newer than the host's cached sync
        # databases. Recheck the remote target privately instead of changing
        # the host DB or mistaking an old cache for a changed target.
        snapshot = native.snapshot(source_db, settings, Path(temp) / 'db',
                                   refresh=bundle.get('metadata_refreshed', False))
        if snapshot['target']['repositories'] != target['repositories']:
            raise RuntimeError('Target repository metadata changed after staging; replan.')
        result = native.prepare(snapshot['database'], settings, archives, cache or Path(temp) / 'native')
        if not result['compatible']:
            raise RuntimeError('The staged candidates no longer prepare successfully with libalpm.')
    return bundle, archives


def transaction_args(bundle_path, output, config_path, source_db, cache):
    # This experimental bridge deliberately cannot be used for a live host upgrade.
    # A production contribution needs a reviewed privileged artifact-import boundary.
    if not (Path('/run/.containerenv').exists() or Path('/.dockerenv').exists()):
        raise RuntimeError('The POC commit bridge is container-only; live Omarchy integration is not enabled.')
    bundle, archives = verify(bundle_path, config_path, source_db, cache)
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    output.chmod(0o711)
    repo = output / 'repository'; repo.mkdir(mode=0o755); repo.chmod(0o755)
    for archive in archives:
        shutil.copyfile(archive, repo / archive.name)
        (repo / archive.name).chmod(0o644)
    native.run(['repo-add', repo / 'omarchy-compat.db.tar.gz', *sorted(repo.glob('*.pkg.tar.*'))])
    for path in repo.iterdir():
        if not path.is_symlink(): path.chmod(0o644)
    # Flatten Includes using pacman's own configuration reader, then insert the
    # temporary repository before all ordinary repositories. Never edit /etc.
    text = native.run(['pacman-conf', '--config', config_path]).stdout
    sections = list(re.finditer(r'^\[([^\]]+)\]\s*$', text, re.MULTILINE))
    first_repo = next((m.start() for m in sections if m.group(1) != 'options'), len(text))
    overlay = ('[omarchy-compat]\nSigLevel = Optional DatabaseOptional\n'
               f'Server = file://{repo}\n\n')
    config = output / 'pacman.conf'
    config.write_text(text[:first_repo] + overlay + text[first_repo:])
    native.json_write(output / 'managed-targets.json', bundle['targets'])
    return ['--config', str(config), *bundle['targets']]
