# SPDX-FileCopyrightText: 2026 Carlos Diaz
# SPDX-License-Identifier: GPL-3.0-or-later
"""Restricted artifact import and native transaction guard.

The installed entry point runs in isolated Python with a fixed environment.
No caller-supplied pacman configuration, command, root or database is accepted.
"""
import json
import hashlib
import os
from pathlib import Path
import pwd
import re
import shutil
import stat
import sys
import tarfile
import tempfile

from . import core, gaze, native

CONFIG = Path('/etc/pacman.conf')
DATABASE = Path('/var/lib/pacman')
STORE = Path('/var/lib/omarchy-update-compat/transactions')
ENTRY = '/usr/lib/omarchy-update-compat/privileged'
ALLOWED = set(gaze.NAMES.values())


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def read_owned(path, owner, limit):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as stream:
        info = os.fstat(stream.fileno())
        require(stat.S_ISREG(info.st_mode) and info.st_uid == owner
                and not info.st_mode & 0o022, 'Input must be a caller-owned regular file, protected from other writers.')
        require(info.st_size <= limit, 'Input exceeds the supported size.')
        data = stream.read(limit + 1)
    require(len(data) <= limit, 'Input exceeds the supported size.')
    return data


def native_arguments(arguments):
    require(arguments and arguments[0] == '-Syu', 'Only a complete -Syu transaction is supported.')
    result, index = ['-Syu'], 1
    while index < len(arguments):
        value = arguments[index]
        if value == '--noconfirm':
            require(value not in result, 'Repeated transaction option.')
            result.append(value)
        elif value == '--overwrite':
            index += 1
            require(index < len(arguments) and arguments[index] == '/usr/share/omarchy/*',
                    'Only the existing Omarchy overwrite rule is supported.')
            result.extend([value, arguments[index]])
        else:
            raise RuntimeError('Unsupported native transaction argument.')
        index += 1
    return result


def validate_settings(settings):
    # The transaction argument whitelist rejects dependency overrides. Preserve
    # repository semantics, or stop when the preparation helper cannot model them.
    for repository in settings['repositories']:
        usage = native.run(['pacman-conf', '--config', CONFIG, '--repo', repository, 'Usage']).stdout.split()
        require(usage == ['All'] or set(usage) == {'Sync', 'Search', 'Install', 'Upgrade'},
                'Restricted repository Usage is unsupported by the compatibility coordinator.')
    require(native.run(['pacman-conf', '--config', CONFIG, 'RootDir']).stdout.strip() == '/',
            'A custom pacman RootDir is unsupported.')
    require(Path(native.run(['pacman-conf', '--config', CONFIG, 'DBPath']).stdout.strip()) == DATABASE,
            'A custom pacman DBPath is unsupported.')


def target_matches(record):
    settings = native.effective_config(CONFIG)
    target = record['target']
    require(settings['architecture'] == target['architecture']
            and settings['repositories'] == target['repository_order']
            and settings['servers'] == target['servers']
            and settings['ignore'] == target['ignore']
            and settings['ignore_groups'] == target['ignore_groups'],
            'Pacman configuration changed after planning; retry the compatibility check.')
    require(native.local_fingerprint(DATABASE) == record['installed_fingerprint'],
            'Installed packages changed after planning; retry the compatibility check.')
    for name, checksum in target['repositories'].items():
        path = DATABASE / 'sync' / f'{name}.db'
        require(path.is_file() and native.digest(path) == checksum,
                'Target repositories changed before commit; retry the compatibility check.')


def verify_transaction(identifier):
    require(re.fullmatch(r'tx-[a-z0-9_]+', identifier), 'Invalid transaction identifier.')
    directory = STORE / identifier
    info = directory.lstat()
    require(stat.S_ISDIR(info.st_mode) and info.st_uid == 0 and not info.st_mode & 0o022,
            'Transaction storage must be root-owned and protected from other writers.')
    record = json.loads(read_owned(directory / 'transaction.json', 0, 1048576))
    current_config = native.run(['pacman-conf', '--config', CONFIG]).stdout
    require(hashlib.sha256(current_config.encode()).hexdigest() == record['native_config_sha256'],
            'Native package configuration changed before commit; replan.')
    require(record['catalog_sha256'] == gaze.catalog()[1], 'Reviewed application catalog changed; replan.')
    approval = Path(pwd.getpwuid(record['caller_uid']).pw_dir) / '.config/omarchy/update-compat.toml'
    require(hashlib.sha256(read_owned(approval, record['caller_uid'], 65536)).hexdigest()
            == record['approval_sha256'], 'Application policy changed before commit; replan.')
    target_matches(record)
    for item in record['packages']:
        path = directory / 'repository' / item['filename']
        require(path.is_file() and not path.is_symlink() and native.digest(path) == item['sha256'],
                'Imported package was changed before commit.')


def import_bundle(bundle_path, caller):
    # Tracking is authorization for this application group, not an arbitrary package installer.
    home = Path(pwd.getpwuid(caller).pw_dir)
    approval = home / '.config/omarchy/update-compat.toml'
    approval_data = read_owned(approval, caller, 65536)
    policies = core.parse_configuration(approval_data.decode())
    require('gaze' in policies, 'Gaze must be explicitly tracked before deploying it.')
    bundle = json.loads(read_owned(bundle_path, caller, 1048576))
    require(bundle.get('schema_version') == 1, 'Unsupported bundle schema.')
    policy, policy_hash = gaze.catalog()
    require(bundle.get('catalog_sha256') == policy_hash, 'Bundle uses a different reviewed application catalog.')
    require(set(bundle.get('targets', [])) == ALLOWED and len(bundle['targets']) == 3,
            'Only the tracked Gaze package group is permitted.')
    require(len(bundle.get('packages', [])) == 3, 'The complete Gaze package group is required.')
    origin = bundle.get('origins', {}).get('gaze')
    require(origin in ['publisher-binary', 'build-source', 'cached-source'], 'Unsupported candidate origin.')
    require(policies['gaze'] != 'upstream-only' or origin == 'publisher-binary',
            'The current upstream-only policy does not authorize a local build.')
    require(policies['gaze'] != 'source' or origin != 'publisher-binary',
            'The current source policy does not authorize the publisher candidate.')
    settings = native.effective_config(CONFIG)
    validate_settings(settings)
    require(native.local_fingerprint(DATABASE) == bundle['installed_fingerprint'],
            'Installed packages changed after staging; replan.')
    installed = native.inventory(DATABASE)
    require(ALLOWED.issubset(installed), 'Deployment cannot introduce a previously absent application.')
    target = bundle['target']
    require(settings['architecture'] == target['architecture']
            and settings['repositories'] == target['repository_order']
            and settings['servers'] == target['servers']
            and settings['ignore'] == target['ignore']
            and settings['ignore_groups'] == target['ignore_groups'],
            'The bundle does not describe this machine\'s native repositories.')
    STORE.mkdir(parents=True, exist_ok=True)
    for path in [STORE, *STORE.parents]:
        info = path.lstat()
        require(stat.S_ISDIR(info.st_mode) and info.st_uid == 0 and not info.st_mode & 0o022,
                'Unsafe transaction storage parent.')
    directory = Path(tempfile.mkdtemp(prefix='tx-', dir=STORE))
    try:
        repo = directory / 'repository'; repo.mkdir(mode=0o755)
        seen, candidates = set(), []
        for item in bundle['packages']:
            name = item['filename']
            require(Path(name).name == name and name.endswith(('.pkg.tar.zst', '.pkg.tar.gz')),
                    'Invalid candidate filename.')
            require(item['package'] in ALLOWED and item['package'] not in seen,
                    'Unexpected or repeated package identity.')
            # Copy first, then verify the copy. Later caller changes cannot alter it.
            source = Path(bundle['repository']) / name
            fd = os.open(source, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            with os.fdopen(fd, 'rb') as incoming, (repo / name).open('xb') as outgoing:
                info = os.fstat(incoming.fileno())
                require(stat.S_ISREG(info.st_mode) and info.st_uid == caller
                        and not info.st_mode & 0o022 and info.st_size <= 1073741824,
                        'Candidate must be a protected caller-owned regular file.')
                remaining = 1073741824
                while block := incoming.read(min(1048576, remaining + 1)):
                    remaining -= len(block)
                    require(remaining >= 0, 'Candidate grew beyond the supported size during import.')
                    outgoing.write(block)
            copied = repo / name
            require(native.digest(copied) == item['sha256'], 'Candidate checksum mismatch.')
            metadata = gaze.metadata(copied)
            require(metadata['pkgname'] == [item['package']] and metadata['pkgver'] == [item['version']],
                    'Candidate identity differs from the approved plan.')
            require(metadata['arch'] == [settings['architecture']], 'Candidate architecture mismatch.')
            require(metadata['pkgver'][0].split('-')[0] == policy['release']['version'],
                    'Candidate version is not covered by the reviewed application catalog.')
            marker = ('omarchy-compat-gaze-publisher-binary' if origin == 'publisher-binary'
                      else 'omarchy-compat-gaze-local-source')
            require(marker in metadata.get('provides', []), 'Candidate origin differs from its package metadata.')
            seen.add(item['package']); candidates.append(copied)
        directory.chmod(0o711)
        snapshot = native.snapshot(DATABASE, settings, directory / 'database', refresh=True)
        require(snapshot['target'] == target, 'Target metadata changed during preparation; replan.')
        result = native.prepare(directory / 'database', settings, candidates, directory / 'native')
        require(result['compatible'], 'Imported candidates do not satisfy the native transaction.')
        native.run(['repo-add', repo / 'omarchy-compat.db.tar.gz', *candidates])
        record = {k: bundle[k] for k in ['target', 'installed_fingerprint', 'packages', 'targets', 'catalog_sha256']}
        record.update(caller_uid=caller, approval_sha256=hashlib.sha256(approval_data).hexdigest())
        native.json_write(directory / 'transaction.json', record)
        hooks = directory / 'hooks'; hooks.mkdir(mode=0o755)
        (hooks / '00-omarchy-compat.hook').write_text(
            '[Trigger]\nOperation = Install\nOperation = Upgrade\nType = Package\nTarget = *\n'
            '[Action]\nDescription = Rechecking approved compatibility target...\nWhen = PreTransaction\n'
            f'Exec = {ENTRY} verify {directory.name}\nAbortOnFail\n')
        text = native.run(['pacman-conf', '--config', CONFIG]).stdout
        record['native_config_sha256'] = hashlib.sha256(text.encode()).hexdigest()
        native.json_write(directory / 'transaction.json', record)
        sections = list(re.finditer(r'^\[([^\]]+)\]\s*$', text, re.MULTILINE))
        first = next(m.start() for m in sections if m.group(1) != 'options')
        text = (text[:first] + f'HookDir = {hooks}\n\n'
                '[omarchy-compat]\nSigLevel = Optional DatabaseOptional\n'
                f'Server = file://{repo}\n\n' + text[first:])
        fd = os.open(directory / 'pacman.conf', os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(fd, 'w') as stream:
            stream.write(text)
        # Only package/repository bytes are public. Flattened configuration can
        # contain private mirror credentials, and must remain root-only.
        directory.chmod(0o711)
        for path in directory.rglob('*'):
            if path.is_file() and not path.is_symlink(): path.chmod(0o600)
            elif path.is_dir(): path.chmod(0o700)
        repo.chmod(0o755)
        for path in repo.rglob('*'):
            if path.is_file() and not path.is_symlink(): path.chmod(0o644)
            elif path.is_dir(): path.chmod(0o755)
        return directory
    except BaseException:
        shutil.rmtree(directory)
        raise


def main(arguments, caller):
    try:
        require(arguments, 'Missing privileged operation.')
        if arguments[0] == 'verify' and len(arguments) == 2:
            verify_transaction(arguments[1])
            return 0
        require(len(arguments) >= 4 and arguments[0] == 'transaction' and arguments[2] == '--',
                'Unsupported privileged operation.')
        flags = native_arguments(arguments[3:])
        directory = import_bundle(arguments[1], caller)
        record = json.loads((directory / 'transaction.json').read_text())
        print('Gaze  INSTALLING With the system update...', flush=True)
        # Exec retains the updater's PID-1 scope and native signal behavior.
        os.execv('/usr/bin/pacman', ['pacman', '--config', str(directory / 'pacman.conf'),
                                   *flags, *record['targets']])
    except (RuntimeError, OSError, ValueError, KeyError, tarfile.TarError) as error:
        print(f'Gaze  BLOCKED    {error}', file=sys.stderr)
        return 20
