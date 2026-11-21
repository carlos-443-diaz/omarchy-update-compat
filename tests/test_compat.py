# SPDX-FileCopyrightText: 2026 Carlos Diaz
# SPDX-License-Identifier: GPL-3.0-or-later
import io
import json
import os
from pathlib import Path
import tarfile
import tempfile
import unittest
from unittest.mock import patch

from omarchy_compat import core, gaze, native, handoff


def desc(name, version, dependencies=(), provides=()):
    values = {'NAME': [name], 'VERSION': [version], 'ARCH': ['x86_64'],
              'FILENAME': [f'{name}-{version}-x86_64.pkg.tar.gz'],
              'DESC': [name], 'SIZE': ['1'], 'ISIZE': ['1'], 'CSIZE': ['1'],
              'DEPENDS': list(dependencies), 'PROVIDES': list(provides)}
    return ''.join(f'%{key}%\n' + '\n'.join(value) + '\n\n'
                   for key, value in values.items() if value)


class Fixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.db = self.root / 'database'
        (self.db / 'local').mkdir(parents=True)
        (self.db / 'local/ALPM_DB_VERSION').write_text('9\n')
        (self.db / 'sync').mkdir()
        self.cache = self.root / 'cache'
        self.settings = {'architecture': 'x86_64', 'repositories': ['core'],
                         'ignore': [], 'ignore_groups': []}
        self.install('opencv', '5.0.0-1')
        self.install('gaze-bin', '0.3.7-1', ['opencv>=5.0', 'opencv<5.1'])
        self.install('gaze-gui-bin', '0.3.7-1', ['opencv>=5.0', 'opencv<5.1'])
        self.install('gaze-omarchy-bin', '0.3.7-1', ['gaze-bin'])
        self.repository('5.0.0-1')

    def install(self, name, version, dependencies=(), provides=()):
        for previous in (self.db / 'local').glob(f'{name}-*/desc'):
            # Only remove exact same-name packages, not prefix matches.
            if native.fields(previous.read_text())['NAME'] == [name]:
                import shutil
                shutil.rmtree(previous.parent)
        directory = self.db / 'local' / f'{name}-{version}'
        directory.mkdir()
        (directory / 'desc').write_text(desc(name, version, dependencies, provides))
        (directory / 'files').write_text('%FILES%\n\n')

    def repository(self, opencv):
        with tarfile.open(self.db / 'sync/core.db', 'w:gz') as tar:
            data = desc('opencv', opencv).encode()
            info = tarfile.TarInfo(f'opencv-{opencv}/desc'); info.size = len(data)
            tar.addfile(info, io.BytesIO(data))

    def package(self, name, version, dependencies=(), provides=(), installer=False):
        path = self.root / f'{name}-{version}-x86_64.pkg.tar.gz'
        data = '\n'.join([f'pkgname = {name}', f'pkgver = {version}',
                          'pkgdesc = fixture', 'arch = x86_64', 'size = 1', 'builddate = 1',
                          *[f'depend = {d}' for d in dependencies],
                          *[f'provides = {d}' for d in provides]]) + '\n'
        files = {'.PKGINFO': data.encode(), f'usr/share/fixtures/{name}': b'fixture\n'}
        if installer:
            files['.INSTALL'] = b'post_install() { :; }\n'
        with tarfile.open(path, 'w:gz') as tar:
            for name_, content in files.items():
                info = tarfile.TarInfo(name_); info.size = len(content); info.mode = 0o644
                tar.addfile(info, io.BytesIO(content))
        return path

    def group(self, abi, source=False):
        version = '0.5.2-1.1' if source else '0.5.2-1'
        marker = ['omarchy-compat-gaze-local-source'] if source else []
        return [self.package('gaze-bin', version, [f'opencv>={abi}', f'opencv<{float(abi)+0.1:.1f}'], marker),
                self.package('gaze-gui-bin', version, [f'opencv>={abi}', f'opencv<{float(abi)+0.1:.1f}']),
                self.package('gaze-omarchy-bin', version, ['gaze-bin'])]

    def prepare(self, candidates=()):
        return native.prepare(self.db, self.settings, candidates, self.cache)


class NativeTests(Fixture):
    def test_publisher_rejected_on_old_abi(self):
        result = self.prepare(self.group('5.1'))
        self.assertFalse(result['compatible'])
        self.assertIn('opencv>=5.1', [m['dependency'] for m in result['missing']])

    def test_local_source_fits_old_abi(self):
        self.assertTrue(self.prepare(self.group('5.0', source=True))['compatible'])

    def test_library_upgrade_alone_is_rejected(self):
        self.repository('5.1.0-1')
        self.assertFalse(self.prepare()['compatible'])

    def test_publisher_and_library_upgrade_prepare_together(self):
        self.repository('5.1.0-1')
        result = self.prepare(self.group('5.1'))
        self.assertTrue(result['compatible'])
        self.assertIn({'package': 'opencv', 'version': '5.1.0-1'}, result['transaction'])

    def test_explicit_handoff_accepts_lower_packaging_revision(self):
        self.install('gaze-bin', '0.5.2-1.1', ['opencv>=5.0'], ['omarchy-compat-gaze-local-source'])
        self.repository('5.1.0-1')
        result = self.prepare(self.group('5.1'))
        self.assertTrue(result['compatible'])
        self.assertIn({'package': 'gaze-bin', 'version': '0.5.2-1'}, result['transaction'])

    def test_native_preparation_does_not_change_local_database(self):
        before = native.local_fingerprint(self.db)
        self.prepare(self.group('5.0', source=True))
        self.assertEqual(before, native.local_fingerprint(self.db))
        self.assertFalse((self.db / 'db.lck').exists())

    @unittest.skipIf(os.geteuid() == 0, 'Native makepkg requires an unprivileged builder')
    def test_native_wrapper_preserves_installer_and_bounds(self):
        original = self.package('gaze', '0.5.2-1', ['opencv>=5.0', 'opencv<5.1'], installer=True)
        wrapped = gaze.wrap([original], 'local-source', self.root / 'wrapped')[0]
        info = gaze.metadata(wrapped)
        self.assertEqual(info['pkgname'], ['gaze-bin'])
        self.assertEqual(info['pkgver'], ['0.5.2-1.1'])
        self.assertIn('opencv<5.1', info['depend'])
        self.assertIn('omarchy-compat-gaze-local-source', info['provides'])
        with tarfile.open(wrapped) as tar:
            self.assertIn(b'post_install', tar.extractfile('.INSTALL').read())


class CoordinatorTests(Fixture):
    def coordinator(self, policy='prefer-upstream'):
        config = self.root / 'compat.toml'
        config.write_text(f'[applications]\ngaze = "{policy}"\n')
        pacman_config = self.root / 'pacman.conf'
        pacman_config.write_text('[options]\nArchitecture = x86_64\n[core]\nServer = https://example.invalid/core/os/x86_64\n')
        return core.Coordinator(config, self.root / 'state', self.root / 'cache', pacman_config, self.db, True)

    def providers(self, publisher, source=None):
        self.release = {'tag': 'v0.5.2', 'version': '0.5.2'}
        self.publisher_mock = patch.object(gaze, 'publisher', return_value=(self.release, publisher))
        self.wrapper_mock = patch.object(gaze, 'wrap', side_effect=lambda paths, *_: paths)
        self.source_mock = patch.object(gaze, 'source_build', return_value=(source, {'fixture': True}))
        self.publisher_mock.start(); self.wrapper_mock.start()
        self.builder = self.source_mock.start()
        self.addCleanup(self.publisher_mock.stop); self.addCleanup(self.wrapper_mock.stop); self.addCleanup(self.source_mock.stop)

    def test_plan_requests_build_without_executing_builder(self):
        self.providers(self.group('5.1'))
        result = self.coordinator().run()
        self.assertEqual(result['status'], 'build-required')
        self.builder.assert_not_called()

    def test_stage_builds_once_then_reuses_valid_local_candidate(self):
        self.providers(self.group('5.1'), self.group('5.0', source=True))
        runner = self.coordinator()
        first = runner.run(stage=True)
        self.assertEqual(first['status'], 'ready')
        self.assertTrue(Path(first['bundle']).exists())
        second = runner.run(stage=True)
        self.assertEqual(second['applications'][0]['action'], 'cached-source')
        self.assertEqual(self.builder.call_count, 1)

    def test_compatible_upstream_avoids_build(self):
        self.repository('5.1.0-1'); self.providers(self.group('5.1'))
        result = self.coordinator().run(stage=True)
        self.assertEqual(result['applications'][0]['action'], 'publisher-binary')
        self.assertEqual(result['status'], 'ready')
        self.builder.assert_not_called()

    def test_upstream_handoff_is_reported(self):
        self.install('gaze-bin', '0.5.2-1.1', ['opencv>=5.0'], ['omarchy-compat-gaze-local-source'])
        self.repository('5.1.0-1'); self.providers(self.group('5.1'))
        result = self.coordinator().run(stage=True)
        self.assertTrue(result['applications'][0]['handoff_to_upstream'])

    def test_upstream_only_blocks_without_build(self):
        self.providers(self.group('5.1'))
        result = self.coordinator('upstream-only').run(stage=True)
        self.assertEqual(result['status'], 'blocked')
        self.assertNotIn('bundle', result)
        self.builder.assert_not_called()

    def test_managed_package_pin_blocks_explicit_replacement(self):
        self.providers(self.group('5.1'), self.group('5.0', source=True))
        runner = self.coordinator()
        config = Path(runner.pacman_config)
        config.write_text(config.read_text().replace('[options]', '[options]\nIgnorePkg = gaze-*'))
        result = runner.run(stage=True)
        self.assertEqual(result['status'], 'blocked')
        self.assertIn('pinned', result['applications'][0]['reason'])
        self.assertNotIn('bundle', result)
        self.builder.assert_not_called()

    def test_build_failure_leaves_installed_state_unchanged(self):
        self.providers(self.group('5.1'))
        self.builder.side_effect = RuntimeError('build failed')
        before = native.local_fingerprint(self.db)
        with self.assertRaisesRegex(RuntimeError, 'build failed'):
            self.coordinator().run(stage=True)
        self.assertEqual(before, native.local_fingerprint(self.db))

    def test_unmanaged_blocker_is_not_silently_repaired(self):
        self.install('other-app', '1-1', ['opencv<5.1'])
        self.repository('5.1.0-1'); self.providers(self.group('5.1'))
        result = self.coordinator().run(stage=True)
        self.assertEqual(result['status'], 'blocked')
        self.builder.assert_not_called()

    def test_corrupt_cached_artifact_is_not_reused(self):
        self.providers(self.group('5.1'), self.group('5.0', source=True))
        runner = self.coordinator(); first = runner.run(stage=True)
        archive = Path(first['applications'][0]['candidates'][0]['path'])
        archive.write_bytes(b'tampered')
        result = runner.run()
        self.assertEqual(result['applications'][0]['action'], 'build-source')

    def staged(self):
        self.providers(self.group('5.1'), self.group('5.0', source=True))
        runner = self.coordinator()
        result = runner.run(stage=True)
        return runner, result

    def test_bundle_verifies_against_unchanged_native_state(self):
        runner, result = self.staged()
        bundle, archives = handoff.verify(result['bundle'], runner.pacman_config, self.db, self.cache)
        self.assertEqual(len(archives), 3)
        self.assertEqual(set(bundle['targets']), set(gaze.NAMES.values()))

    def test_bundle_rejects_changed_target_metadata(self):
        runner, result = self.staged()
        self.repository('5.1.0-1')
        with self.assertRaisesRegex(RuntimeError, 'metadata changed'):
            handoff.verify(result['bundle'], runner.pacman_config, self.db, self.cache)

    def test_bundle_rejects_changed_pins(self):
        runner, result = self.staged()
        config = Path(runner.pacman_config)
        config.write_text(config.read_text().replace('[options]', '[options]\nIgnorePkg = opencv'))
        with self.assertRaisesRegex(RuntimeError, 'configuration changed'):
            handoff.verify(result['bundle'], runner.pacman_config, self.db, self.cache)

    def test_refreshed_target_can_be_newer_than_host_sync_cache(self):
        import shutil
        self.providers(self.group('5.1'), self.group('5.0', source=True))
        runner = self.coordinator()
        repository = self.root / 'remote-repository'; repository.mkdir()
        self.repository('5.0.0-2')
        shutil.copyfile(self.db / 'sync/core.db', repository / 'core.db')
        self.repository('5.0.0-1')
        # pacman uses conditional downloads; give its old cache an old mtime.
        os.utime(self.db / 'sync/core.db', (1, 1))
        Path(runner.pacman_config).write_text(
            f'[options]\nArchitecture = x86_64\nSigLevel = Never\n[core]\nServer = file://{repository}\n')
        before = native.digest(self.db / 'sync/core.db')
        result = runner.run(stage=True, refresh=True)
        self.assertEqual(result['status'], 'ready')
        self.assertNotEqual(before, result['snapshot']['target']['repositories']['core'])
        handoff.verify(result['bundle'], runner.pacman_config, self.db, self.cache)
        self.assertEqual(before, native.digest(self.db / 'sync/core.db'))

    def test_bundle_rejects_changed_installed_state(self):
        runner, result = self.staged()
        self.install('other-app', '1-1')
        with self.assertRaisesRegex(RuntimeError, 'Installed package state changed'):
            handoff.verify(result['bundle'], runner.pacman_config, self.db, self.cache)

    def test_bundle_rejects_modified_staged_archive(self):
        runner, result = self.staged()
        bundle = json.loads(Path(result['bundle']).read_text())
        archive = Path(bundle['repository']) / bundle['packages'][0]['filename']
        archive.write_bytes(b'replaced')
        with self.assertRaisesRegex(RuntimeError, 'replaced or modified'):
            handoff.verify(result['bundle'], runner.pacman_config, self.db, self.cache)

    def test_compatible_cached_build_survives_unrelated_snapshot_change(self):
        runner, _ = self.staged()
        self.repository('5.0.0-2')
        result = runner.run(stage=True)
        self.assertEqual(result['applications'][0]['action'], 'cached-source')
        self.assertEqual(self.builder.call_count, 1)

    def test_tracking_does_not_install_an_absent_application(self):
        import shutil
        for path in (self.db / 'local').glob('gaze*-*/desc'):
            shutil.rmtree(path.parent)
        self.providers(self.group('5.1'))
        result = self.coordinator().run(stage=True)
        self.assertEqual(result['applications'][0]['action'], 'not-installed')
        self.builder.assert_not_called()

    def test_live_commit_bridge_is_disabled(self):
        with patch.object(handoff.Path, 'exists', return_value=False):
            with self.assertRaisesRegex(RuntimeError, 'container-only'):
                handoff.transaction_args('unused', 'unused', 'unused', 'unused', 'unused')


class PolicyTests(unittest.TestCase):
    def test_source_policy_keeps_source_even_when_publisher_fits(self):
        self.assertEqual(core.selection('source', {'compatible': True}, {'compatible': True}), 'cached-source')

    def test_failure_discovery_is_only_a_suggestion(self):
        result = core.discover("breaks dependency 'opencv<5.1' required by gaze-bin; $(touch /tmp/never)", {})
        self.assertEqual(result[0]['application'], 'gaze')
        self.assertFalse(result[0]['tracked'])
        self.assertEqual(core.discover('not-gaze-bin-helper', {}), [])

    def test_unknown_adapter_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'compat.toml'
            path.write_text('[applications]\narbitrary = "prefer-upstream"\n')
            with self.assertRaisesRegex(ValueError, 'No reviewed adapter'):
                core.configuration(path)


if __name__ == '__main__':
    unittest.main()
