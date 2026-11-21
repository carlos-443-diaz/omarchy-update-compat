# SPDX-FileCopyrightText: 2026 Carlos Diaz
# SPDX-License-Identifier: GPL-3.0-or-later
"""Native tools and isolated snapshots of the package database."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import platform

ROOT = Path(__file__).resolve().parents[1]


def run(args, *, env=None, cwd=None, accepted=(0,)):
    result = subprocess.run([str(a) for a in args], text=True, capture_output=True,
                            env=env, cwd=cwd)
    if result.returncode not in accepted:
        raise RuntimeError(f"{args[0]} exited {result.returncode}: "
                           f"{result.stderr.strip() or result.stdout[-3000:]}")
    return result


def digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def json_write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile('w', dir=path.parent, delete=False) as out:
        json.dump(value, out, indent=2)
        out.write('\n')
        name = out.name
    os.replace(name, path)


def fields(text):
    """Parse libalpm's own %FIELD% database format."""
    result, key = {}, None
    for line in text.splitlines():
        if line.startswith('%') and line.endswith('%'):
            key = line.strip('%'); result[key] = []
        elif line and key:
            result[key].append(line)
    return result


def inventory(dbpath):
    result = {}
    for desc in sorted((Path(dbpath) / 'local').glob('*/desc')):
        item = fields(desc.read_text())
        if item.get('NAME') and item.get('VERSION'):
            result[item['NAME'][0]] = {
                'version': item['VERSION'][0], 'provides': item.get('PROVIDES', []),
                'depends': item.get('DEPENDS', []), 'groups': item.get('GROUPS', [])}
    return result


def local_fingerprint(dbpath):
    return hashlib.sha256(json.dumps(inventory(dbpath), sort_keys=True).encode()).hexdigest()


def effective_config(config):
    def values(*args):
        return run(['pacman-conf', '--config', config, *args]).stdout.splitlines()
    repos = values('--repo-list')
    return {'path': str(Path(config).resolve()), 'repositories': repos,
            'architecture': values('Architecture')[0],
            'ignore': values('IgnorePkg'), 'ignore_groups': values('IgnoreGroup'),
            'servers': {r: values('--repo', r, 'Server') for r in repos}}


def snapshot(source_db, config, destination, refresh=False):
    source_db, destination = Path(source_db), Path(destination)
    if (source_db / 'db.lck').exists():
        raise RuntimeError('A native package transaction is in progress; try again afterward.')
    before = local_fingerprint(source_db)
    destination.mkdir(parents=True)
    shutil.copytree(source_db / 'local', destination / 'local')
    shutil.copytree(source_db / 'sync', destination / 'sync')
    if before != local_fingerprint(source_db):
        raise RuntimeError('Installed package state changed during discovery; retry.')
    if refresh:
        # Same unprivileged metadata-refresh technique used by checkupdates.
        run(['fakeroot', 'pacman', '--config', config['path'], '--dbpath', destination,
             '--logfile', '/dev/null', '-Sy', '--noconfirm', '--disable-sandbox-filesystem'])
    repo_hashes = {}
    for repo in config['repositories']:
        file = destination / 'sync' / f'{repo}.db'
        if not file.exists():
            raise RuntimeError(f'Missing target database: {repo}; refresh metadata first.')
        repo_hashes[repo] = digest(file)
    target = {'architecture': config['architecture'], 'repositories': repo_hashes,
              'repository_order': config['repositories'], 'servers': config['servers'],
              'ignore': config.get('ignore', []), 'ignore_groups': config.get('ignore_groups', [])}
    return {'database': str(destination), 'installed_fingerprint': before,
            'target': target,
            'target_fingerprint': hashlib.sha256(json.dumps(target, sort_keys=True).encode()).hexdigest(),
            'metadata_refreshed': refresh}


def prepare(dbpath, config, candidates, cache):
    source = ROOT / 'native' / 'prepare.c'
    key = hashlib.sha256(source.read_bytes() + Path('/usr/include/alpm.h').read_bytes()
                         + platform.machine().encode()).hexdigest()[:16]
    helper = Path(cache) / f'prepare-{key}'
    helper.parent.mkdir(parents=True, exist_ok=True)
    if not helper.exists():
        temporary = helper.with_suffix(f'.{os.getpid()}')
        run(['cc', '-O2', '-Wall', '-Wextra', '-Werror', source, '-lalpm', '-o', temporary])
        os.replace(temporary, helper)
    args = [helper, '/', str(dbpath), config['architecture']]
    for repo in config['repositories']:
        args += ['--repo', repo]
    for name in config.get('ignore', []):
        args += ['--ignore', name]
    for name in config.get('ignore_groups', []):
        args += ['--ignore-group', name]
    for path in candidates:
        args += ['--package', str(path)]
    return json.loads(run(args, accepted=(0, 20)).stdout)
