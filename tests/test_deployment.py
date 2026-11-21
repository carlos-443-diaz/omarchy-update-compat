# SPDX-License-Identifier: GPL-3.0-or-later
import contextlib
import fcntl
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import shutil

from omarchy_compat import core, gaze, native, privileged, workflow
import test_compat as fixtures


class BoundaryTests(unittest.TestCase):
    def test_only_native_full_upgrade_flags_are_accepted(self):
        flags = ['-Syu', '--noconfirm', '--overwrite', '/usr/share/omarchy/*']
        self.assertEqual(privileged.native_arguments(flags), flags)
        for flags in [[], ['-U', 'app'], ['-Syu', '--config', '/tmp/untrusted'],
                      ['-Syu', '--nodeps'], ['-Syu', '--overwrite', '*'],
                      ['-Syu', '--dbpath', '/tmp/other'], ['-Syu', 'other-app']]:
            with self.subTest(flags=flags), self.assertRaises(RuntimeError):
                privileged.native_arguments(flags)

    def test_owned_input_rejects_links_and_other_writers(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'data'; path.write_text('{}')
            self.assertEqual(privileged.read_owned(path, os.getuid(), 100), b'{}')
            path.chmod(0o666)
            with self.assertRaises(RuntimeError): privileged.read_owned(path, os.getuid(), 100)
            path.chmod(0o600)
            link = path.with_name('link'); link.symlink_to(path)
            with self.assertRaises(OSError): privileged.read_owned(link, os.getuid(), 100)
            with self.assertRaises(RuntimeError): privileged.read_owned(path, os.getuid() + 1, 100)

    def test_owned_input_is_bounded(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'data'; path.write_bytes(b'x' * 101)
            with self.assertRaises(RuntimeError): privileged.read_owned(path, os.getuid(), 100)


class DeploymentTests(fixtures.Fixture):
    coordinator = fixtures.CoordinatorTests.coordinator
    providers = fixtures.CoordinatorTests.providers
    staged = fixtures.CoordinatorTests.staged
    def test_concurrent_run_is_rejected_then_retry_succeeds(self):
        self.providers(self.group('5.1'), self.group('5.0', source=True))
        runner = self.coordinator()
        with (runner.state / 'coordinator.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with self.assertRaisesRegex(RuntimeError, 'Another compatibility coordinator'):
                runner.run(stage=True)
            self.builder.assert_not_called()
        self.assertEqual(runner.run(stage=True)['status'], 'ready')

    def test_native_package_lock_prevents_building_and_retry_succeeds(self):
        self.providers(self.group('5.1'), self.group('5.0', source=True))
        runner = self.coordinator(); lock = self.db / 'db.lck'; lock.touch()
        with self.assertRaisesRegex(RuntimeError, 'native package transaction'):
            runner.run(stage=True)
        self.builder.assert_not_called()
        lock.unlink()
        self.assertEqual(runner.run(stage=True)['status'], 'ready')

    def test_repeated_upgrade_keeps_installed_group_without_reinstall(self):
        self.providers(self.group('5.1'), self.group('5.0', source=True))
        runner = self.coordinator(); first = runner.run(stage=True)
        for item in first['applications'][0]['candidates']:
            metadata = gaze.metadata(item['path'])
            self.install(item['package'], item['version'], metadata.get('depend', []), metadata.get('provides', []))
        events = []; runner.event = lambda phase, message: events.append(phase)
        second = runner.run(stage=True)
        self.assertTrue(second['applications'][0]['already_installed'])
        self.assertNotIn('bundle', second)
        self.assertIn('KEEP', events)
        self.assertNotIn('READY', events)
        self.assertEqual(self.builder.call_count, 1)
        with contextlib.redirect_stdout(io.StringIO()) as output:
            workflow.verify_installed(second['plan_file'], self.db)
        self.assertIn('KEEP', output.getvalue())

    def test_working_installed_build_survives_source_cache_removal(self):
        self.providers(self.group('5.1'), self.group('5.0', source=True))
        runner = self.coordinator(); first = runner.run(stage=True)
        for item in first['applications'][0]['candidates']:
            metadata = gaze.metadata(item['path'])
            self.install(item['package'], item['version'], metadata.get('depend', []), metadata.get('provides', []))
        shutil.rmtree(runner.cache / 'source-index')
        result = runner.run(stage=True)
        self.assertEqual(result['applications'][0]['action'], 'keep-installed')
        self.assertNotIn('bundle', result)
        self.assertEqual(self.builder.call_count, 1)
        self.assertEqual(workflow.bundle_targets(result), sorted(gaze.NAMES.values()))
        with contextlib.redirect_stdout(io.StringIO()) as output:
            workflow.verify_installed(result['plan_file'], self.db)
        self.assertIn('KEEP', output.getvalue())

    def test_done_requires_the_selected_packages_to_be_installed(self):
        runner, result = self.staged()
        with contextlib.redirect_stdout(io.StringIO()) as output, self.assertRaises(RuntimeError):
            workflow.verify_installed(result['plan_file'], self.db)
        self.assertNotIn('DONE', output.getvalue())

    def test_failed_build_never_reports_ready(self):
        self.providers(self.group('5.1'))
        self.builder.side_effect = RuntimeError('compiler failed')
        runner = self.coordinator(); events = []
        runner.event = lambda phase, message: events.append(phase)
        with self.assertRaises(RuntimeError): runner.run(stage=True)
        self.assertIn('BUILDING', events)
        self.assertNotIn('READY', events)
        self.assertNotIn('DONE', events)


@unittest.skipUnless(os.geteuid() == 0, 'Privileged import tests run inside the disposable container')
class RootImportTests(fixtures.Fixture):
    coordinator = fixtures.CoordinatorTests.coordinator
    providers = fixtures.CoordinatorTests.providers

    def setUp(self):
        super().setUp()
        self.store = Path(tempfile.mkdtemp(prefix='compat-root-test-', dir='/var/lib'))
        self.addCleanup(shutil.rmtree, self.store)
        approval = Path.home() / '.config/omarchy/update-compat.toml'
        previous = approval.read_bytes() if approval.exists() else None
        approval.parent.mkdir(parents=True, exist_ok=True)
        approval.write_text('[applications]\ngaze = "prefer-upstream"\n'); approval.chmod(0o600)
        self.approval = approval
        def restore():
            if previous is None: approval.unlink(missing_ok=True)
            else: approval.write_bytes(previous)
        self.addCleanup(restore)
        publisher = self.group('5.1')
        marker = ['omarchy-compat-gaze-local-source']
        source = [self.package('gaze-bin', '0.5.2-1.1', ['opencv>=5.0', 'opencv<5.1'], marker),
                  self.package('gaze-gui-bin', '0.5.2-1.1', ['opencv>=5.0', 'opencv<5.1'], marker),
                  self.package('gaze-omarchy-bin', '0.5.2-1.1', ['gaze-bin'], marker)]
        self.providers(publisher, source)
        self.runner = self.coordinator()
        remote = self.root / 'remote'; remote.mkdir()
        shutil.copyfile(self.db / 'sync/core.db', remote / 'core.db')
        Path(self.runner.pacman_config).write_text(
            f'[options]\nArchitecture = x86_64\nSigLevel = Never\nDBPath = {self.db}\n'
            f'[core]\nServer = file://{remote}\n')
        result = self.runner.run(stage=True)
        self.bundle = Path(result['bundle'])
        for name, value in [('CONFIG', Path(self.runner.pacman_config)), ('DATABASE', self.db), ('STORE', self.store)]:
            context = patch.object(privileged, name, value); context.start(); self.addCleanup(context.stop)

    def test_import_copies_and_verifies_then_guard_accepts(self):
        directory = privileged.import_bundle(self.bundle, 0)
        privileged.verify_transaction(directory.name)
        self.assertIn('HookDir = ', (directory / 'pacman.conf').read_text())
        self.assertEqual((directory / 'pacman.conf').stat().st_uid, 0)
        self.assertEqual((directory / 'pacman.conf').stat().st_mode & 0o777, 0o600)
        self.assertEqual((directory / 'transaction.json').stat().st_mode & 0o777, 0o600)
        self.assertEqual((directory / 'repository').stat().st_mode & 0o777, 0o755)
        original = json.loads(self.bundle.read_text())
        (Path(original['repository']) / original['packages'][0]['filename']).write_bytes(b'changed later')
        privileged.verify_transaction(directory.name)

    def test_guard_rejects_target_drift(self):
        directory = privileged.import_bundle(self.bundle, 0)
        self.repository('5.1.0-1')
        with self.assertRaisesRegex(RuntimeError, 'Target repositories changed'):
            privileged.verify_transaction(directory.name)

    def test_guard_rejects_policy_change(self):
        directory = privileged.import_bundle(self.bundle, 0)
        self.approval.write_text('[applications]\ngaze = "upstream-only"\n')
        with self.assertRaisesRegex(RuntimeError, 'policy changed'):
            privileged.verify_transaction(directory.name)

    def test_guard_rejects_native_signature_configuration_change(self):
        directory = privileged.import_bundle(self.bundle, 0)
        config = Path(self.runner.pacman_config)
        config.write_text(config.read_text().replace('SigLevel = Never', 'SigLevel = Required'))
        with self.assertRaisesRegex(RuntimeError, 'Native package configuration changed'):
            privileged.verify_transaction(directory.name)

    def test_import_rejects_new_untracked_targets(self):
        data = json.loads(self.bundle.read_text()); data['targets'][0] = 'untracked-package'
        self.bundle.write_text(json.dumps(data))
        with self.assertRaisesRegex(RuntimeError, 'tracked Gaze'):
            privileged.import_bundle(self.bundle, 0)

    def test_import_rejects_a_changed_archive(self):
        data = json.loads(self.bundle.read_text())
        (Path(data['repository']) / data['packages'][0]['filename']).write_bytes(b'tampered')
        with self.assertRaisesRegex(RuntimeError, 'checksum mismatch'):
            privileged.import_bundle(self.bundle, 0)

    def test_import_respects_current_upstream_only_policy(self):
        self.approval.write_text('[applications]\ngaze = "upstream-only"\n')
        with self.assertRaisesRegex(RuntimeError, 'upstream-only'):
            privileged.import_bundle(self.bundle, 0)
