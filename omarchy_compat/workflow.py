# SPDX-FileCopyrightText: 2026 Carlos Diaz
# SPDX-License-Identifier: GPL-3.0-or-later
"""User-visible phases and the opt-in stock Omarchy update entry point."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

from . import core, gaze, native

PACKAGE_ROOT = Path('/usr/lib/omarchy-update-compat')
PRIVILEGED = PACKAGE_ROOT / 'privileged'


def indicator(phase, message):
    print(f'Gaze  {phase:<10} {message}', flush=True)


def bundle_targets(plan):
    if not any(a.get('candidates') or a.get('installed_selection') for a in plan.get('applications', [])):
        return []
    return sorted(gaze.NAMES.values())


def session(path):
    plan = json.loads(Path(path).read_text())
    if plan.get('status') != 'ready':
        raise RuntimeError('The compatibility session is not ready.')
    return plan


def prepare(args, path):
    if not PRIVILEGED.is_file():
        raise RuntimeError('Install the omarchy-update-compat Arch package before automatic deployment.')
    coordinator = core.Coordinator(args.config, args.state_dir, args.cache_dir,
                                   args.pacman_config, args.dbpath, args.offline,
                                   event=indicator)
    try:
        plan = coordinator.run(stage=True, refresh=True)
    except (RuntimeError, OSError, ValueError, KeyboardInterrupt):
        indicator('BLOCKED', 'Preparation failed; the main package upgrade was not started.')
        raise
    if plan['status'] != 'ready':
        for item in plan['applications']:
            indicator('BLOCKED', item['reason'])
        raise RuntimeError('No approved compatible transaction is available.')
    native.json_write(path, plan)
    return plan


def verify_installed(path, source_db='/var/lib/pacman'):
    plan = session(path)
    installed = native.inventory(source_db)
    for application in plan['applications']:
        if application.get('installed_selection'):
            if any(installed.get(name) != info for name, info in application['installed_selection'].items()):
                raise RuntimeError('Installed Gaze changed from the checked selection; inspect the update result.')
            indicator('KEEP', 'Installed build still works; nothing to install.')
            continue
        if not application.get('candidates'):
            continue
        for candidate in application['candidates']:
            info = gaze.metadata(candidate['path'])
            actual = installed.get(candidate['package'], {})
            if not core.installed_matches(actual, info):
                raise RuntimeError('Installed Gaze differs from the selected package; the update is incomplete.')
        if application.get('already_installed'):
            indicator('KEEP', 'Installed build still works; nothing to install.')
        else:
            origin = 'published package' if application['action'] == 'publisher-binary' else 'local build'
            indicator('DONE', f'Installed {application["release"]["version"]} — {origin}.')


def upgrade(yes=False, offline=False):
    if os.geteuid() == 0:
        raise RuntimeError('Run the upgrade as your desktop user; builds must remain unprivileged.')
    if not PRIVILEGED.is_file():
        raise RuntimeError('Install the omarchy-update-compat Arch package before running an automated upgrade.')
    updater = shutil.which('omarchy-update')
    if not updater:
        raise RuntimeError('The installed Omarchy updater is unavailable.')
    with tempfile.TemporaryDirectory(prefix='omarchy-compat-update-') as work:
        env = os.environ.copy()
        env['OMARCHY_COMPAT_SESSION'] = str(Path(work) / 'plan.json')
        env['OMARCHY_COMPAT_OFFLINE'] = '1' if offline else '0'
        capability = Path('/usr/share/omarchy/default/update-compatibility-api')
        if not capability.is_file() or capability.read_text().strip() != '1':
            env['PATH'] = str(PACKAGE_ROOT / 'wrappers') + ':' + env['PATH']
        arguments = [updater, *(['-y'] if yes else [])]
        return subprocess.run(arguments, env=env).returncode


def apply(bundle, flags):
    from .privileged import native_arguments
    flags = native_arguments(flags)
    if not PRIVILEGED.is_file():
        raise RuntimeError('The packaged privileged handoff is not installed.')
    scope = ['systemd-run', '--scope', '--quiet', '--collect'] if Path('/run/systemd/system').is_dir() else []
    os.execvp('sudo', ['sudo', 'env', 'OMARCHY_UPDATE_PACMAN=1', *scope,
                       str(PRIVILEGED), 'transaction', str(bundle), '--', *flags])


def yay(arguments):
    path = os.environ.get('OMARCHY_COMPAT_SESSION')
    managed = bundle_targets(session(path)) if path and Path(path).is_file() else []
    if '-Sua' in arguments and managed:
        arguments = list(arguments)
        if '--ignore' in arguments:
            index = arguments.index('--ignore') + 1
            if index >= len(arguments): raise RuntimeError('Missing AUR ignore value.')
            arguments[index] += ',' + ','.join(managed)
        else:
            arguments += ['--ignore', ','.join(managed)]
    os.execv('/usr/bin/yay', ['yay', *arguments])
