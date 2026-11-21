#!/usr/bin/python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Actual native commit/abort proof using small ABI fixtures, never host data."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from unittest.mock import patch

from omarchy_compat import core, gaze, native, privileged
sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_compat import Fixture

assert os.geteuid() == 0 and Path('/run/.containerenv').exists()
assert os.environ.get('OMARCHY_COMPAT_NATIVE_FIXTURE') == 'disposable-only'
fixture = Fixture(); fixture.setUp()
try:
    marker = ['omarchy-compat-gaze-local-source']
    fixture.install('gaze-bin', '0.5.2-1.1', ['opencv>=5.0', 'opencv<5.1'], marker)
    fixture.install('gaze-gui-bin', '0.5.2-1.1', ['opencv>=5.0', 'opencv<5.1'], marker)
    fixture.install('gaze-omarchy-bin', '0.5.2-1.1', ['gaze-bin'], marker)
    for path in (fixture.db / 'local').glob('*/desc'):
        fields = native.fields(path.read_text())
        path.write_text(''.join(f'%{key}%\n' + '\n'.join(values) + '\n\n'
                                for key, values in fields.items() if key not in {'FILENAME', 'ISIZE', 'CSIZE'}))
    database = Path('/var/lib/pacman')
    shutil.rmtree(database)
    shutil.copytree(fixture.db, database)
    remote = Path('/var/lib/compat-fixture-repository'); remote.mkdir()
    os.utime(database / 'sync/core.db', (1, 1))
    publication = 10
    def publish(version):
        global publication
        package = fixture.package('opencv', version)
        copied = remote / package.name; shutil.copyfile(package, copied)
        native.run(['repo-add', remote / 'core.db.tar.gz', copied])
        os.utime(remote / 'core.db.tar.gz', (publication, publication))
        publication += 10
    publish('5.1.0-1')
    config = Path('/etc/pacman.conf')
    config.write_text(f'[options]\nArchitecture = x86_64\nSigLevel = Never\n[core]\nServer = file://{remote}\n')
    approval = Path('/root/.config/omarchy/update-compat.toml')
    approval.parent.mkdir(parents=True, exist_ok=True)
    approval.write_text('[applications]\ngaze = "prefer-upstream"\n'); approval.chmod(0o600)
    marker = ['omarchy-compat-gaze-publisher-binary']
    packages = [fixture.package('gaze-bin', '0.5.2-1', ['opencv>=5.1', 'opencv<5.2'], marker),
                fixture.package('gaze-gui-bin', '0.5.2-1', ['opencv>=5.1', 'opencv<5.2'], marker),
                fixture.package('gaze-omarchy-bin', '0.5.2-1', ['gaze-bin'], marker)]
    for package in packages:
        package.chmod(0o644)
    release = dict(gaze.catalog()[0]['release'], catalog_sha256=gaze.catalog()[1])
    with patch.object(gaze, 'publisher', return_value=(release, packages)), patch.object(gaze, 'wrap', side_effect=lambda p,*_:p):
        runner = core.Coordinator(approval, fixture.root / 'state', fixture.cache, config, database, True)
        plan = runner.run(stage=True, refresh=True)
        assert plan['applications'][0]['handoff_to_upstream']
        directory = privileged.import_bundle(plan['bundle'], 0)
        before = native.local_fingerprint(database)
        # A changed target still satisfies the ABI bounds, but is not what was approved.
        publish('5.1.1-1')
        result = subprocess.run(['/usr/bin/pacman', '--config', str(directory / 'pacman.conf'),
                                 '-Syu', '--noconfirm', *sorted(gaze.NAMES.values())], capture_output=True, text=True)
        print(result.stdout, result.stderr)
        assert result.returncode != 0 and 'Target repositories changed before commit' in result.stdout + result.stderr
        assert native.local_fingerprint(database) == before
        print('PASS: real pretransaction hook rejected drift before changing installed packages')
        plan = runner.run(stage=True, refresh=True)
        assert plan['applications'][0]['handoff_to_upstream']
        directory = privileged.import_bundle(plan['bundle'], 0)
        conflict = Path('/usr/share/fixtures/gaze-bin')
        conflict.parent.mkdir(parents=True, exist_ok=True); conflict.write_text('unowned file')
        before = native.local_fingerprint(database)
        result = subprocess.run(['/usr/bin/pacman', '--config', str(directory / 'pacman.conf'),
                                 '-Syu', '--noconfirm', *sorted(gaze.NAMES.values())], capture_output=True, text=True)
        print(result.stdout, result.stderr)
        assert result.returncode != 0 and 'exists in filesystem' in result.stdout + result.stderr
        assert native.local_fingerprint(database) == before
        conflict.unlink()
        print('PASS: native file-conflict checking remained active and preserved installed state')
        subprocess.run(['/usr/bin/pacman', '--config', str(directory / 'pacman.conf'),
                        '-Syu', '--noconfirm', *sorted(gaze.NAMES.values())], check=True)
        installed = native.inventory(database)
        assert installed['opencv']['version'] == '5.1.1-1'
        for name in gaze.NAMES.values():
            assert installed[name]['version'] == '0.5.2-1'
            assert 'omarchy-compat-gaze-publisher-binary' in installed[name]['provides']
        repeated = runner.run(stage=True, refresh=True)
        assert repeated['applications'][0]['already_installed'] and 'bundle' not in repeated
        print('PASS: real pacman committed library upgrade and lower-revision publisher handoff together; repeat kept it')
finally:
    fixture.doCleanups()
