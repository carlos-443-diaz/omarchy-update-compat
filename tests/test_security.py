# SPDX-License-Identifier: GPL-3.0-or-later
"""Regressions for hanging/flooding builders and unreviewed build inputs."""
import json
import os
from pathlib import Path
import sys
import signal
import tarfile
import tempfile
import time
import unittest
from unittest.mock import patch

from omarchy_compat import gaze, limits
import test_compat as fixtures


class ExecutionTests(unittest.TestCase):
    def test_hung_descendant_is_stopped_when_its_parent_has_exited(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            script = ('import os,time,signal; pid=os.fork(); '
                      'open("' + str(root / 'pid') + '","w").write(str(pid or os.getpid())); '
                      'os._exit(0) if pid else (signal.signal(signal.SIGTERM,signal.SIG_IGN),time.sleep(60))')
            started = time.monotonic()
            with self.assertRaisesRegex(RuntimeError, 'time budget'):
                limits.command([sys.executable, '-c', script], root / 'log', timeout=0.3)
            self.assertLess(time.monotonic() - started, 7)
            pid = int((root / 'pid').read_text())
            status = Path(f'/proc/{pid}/stat')
            deadline = time.monotonic() + 1
            while status.exists() and status.read_text().split()[2] != 'Z' and time.monotonic() < deadline:
                time.sleep(0.01)
            if status.exists():
                self.assertEqual(status.read_text().split()[2], 'Z')

    def test_output_flood_is_bounded_and_partial_archive_removed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); output = root / 'archive'
            with self.assertRaisesRegex(RuntimeError, 'size budget'):
                limits.command([sys.executable, '-c', 'import os; os.write(1,b"x"*100000)'],
                               root / 'log', timeout=3, output=output, output_limit=1024)
            self.assertFalse(output.exists())

    def test_log_cap_applies_across_phases(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(limits, 'LOG_BYTES', 1024):
            log = Path(directory) / 'log'
            for _ in range(2):
                limits.command([sys.executable, '-c', 'import os; os.write(2,b"x"*5000)'], log, timeout=3)
            self.assertEqual(log.stat().st_size, 1024)


class TrustTests(unittest.TestCase):
    def test_mirror_credentials_never_enter_a_build_command(self):
        policy, checksum = gaze.catalog()
        release = dict(policy['release'], catalog_sha256=checksum)
        target = {'architecture': 'x86_64', 'servers': {'extra': ['https://user:private@mirror.example/extra/os/x86_64']}}
        with tempfile.TemporaryDirectory() as directory, patch.object(gaze, 'run') as execute:
            with self.assertRaisesRegex(RuntimeError, 'credential-free HTTPS'):
                gaze.source_build(release, target, directory, Path(directory) / 'log')
            execute.assert_not_called()

    def test_non_string_policy_has_a_clear_configuration_error(self):
        from omarchy_compat.core import parse_configuration
        with self.assertRaises(ValueError):
            parse_configuration('[applications]\ngaze = []\n')

    def test_builder_rejects_a_mirror_that_advanced_during_environment_creation(self):
        target = {'repositories': {'core': 'a' * 64, 'extra': 'b' * 64}}
        checksums = f'{"a" * 64}  /var/lib/pacman/sync/core.db\n{"b" * 64}  /var/lib/pacman/sync/extra.db\n'
        gaze.verify_build_target(checksums, target)
        with self.assertRaisesRegex(RuntimeError, 'differ from the planned system target'):
            gaze.verify_build_target(checksums.replace('b' * 64, 'c' * 64), target)

    def test_archive_expansion_is_rejected_before_reading_the_payload(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'bomb.tar'
            member = tarfile.TarInfo('payload'); member.size = 1024**3
            path.write_bytes(member.tobuf() + b'\0' * 1024)
            with self.assertRaisesRegex(RuntimeError, 'archive budget'):
                gaze.metadata(path)

    def test_termination_runs_cleanup_and_restores_the_original_handler(self):
        original = signal.getsignal(signal.SIGTERM)
        cleaned = []
        with self.assertRaisesRegex(RuntimeError, 'Build cancelled'):
            with limits.interruptible():
                try:
                    os.kill(os.getpid(), signal.SIGTERM)
                finally:
                    cleaned.append(True)
        self.assertEqual(cleaned, [True])
        self.assertEqual(signal.getsignal(signal.SIGTERM), original)

    def test_recipe_edit_requires_reviewed_catalog_change(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); (root / 'policies').mkdir()
            (root / 'recipe').write_text('changed')
            (root / 'policies/gaze.json').write_text(json.dumps({
                'schema_version': 1, 'recipe_sha256': {'recipe': '0' * 64}}))
            with patch.object(gaze, 'ROOT', root), self.assertRaisesRegex(RuntimeError, 'reviewed trust policy'):
                gaze.catalog()

    def test_unsupported_architecture_never_downloads_a_recipe(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(gaze, 'fetch') as download:
            with self.assertRaisesRegex(RuntimeError, 'No reviewed aarch64'):
                gaze.publisher(directory, 'aarch64')
            download.assert_not_called()


class CacheTrustTests(fixtures.Fixture):
    coordinator = fixtures.CoordinatorTests.coordinator
    providers = fixtures.CoordinatorTests.providers

    def test_changed_reviewed_release_invalidates_legacy_source_cache(self):
        self.providers(self.group('5.1'), self.group('5.0', source=True))
        runner = self.coordinator(); runner.run(stage=True)
        self.release['catalog_sha256'] = 'a-new-reviewed-catalog'
        result = runner.run()
        self.assertEqual(result['applications'][0]['action'], 'build-source')

    def test_newer_installed_application_is_not_silently_downgraded(self):
        self.install('gaze-bin', '9.0.0-1')
        self.providers(self.group('5.1'))
        with self.assertRaisesRegex(RuntimeError, 'newer than the reviewed catalog'):
            self.coordinator().run(stage=True)
        self.builder.assert_not_called()
