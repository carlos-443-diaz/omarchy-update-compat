# SPDX-FileCopyrightText: 2026 Carlos Diaz
# SPDX-License-Identifier: GPL-3.0-or-later
"""Small policy coordinator: native dependency checks decide installability."""
from datetime import datetime, timezone
import fcntl
from fnmatch import fnmatchcase
import json
import re
from pathlib import Path
import shutil
import sys
import tomllib
import uuid

from . import gaze, native

POLICIES = {'prefer-upstream', 'source', 'upstream-only'}


def configuration(path):
    path = Path(path)
    if not path.exists():
        return {}
    config = tomllib.loads(path.read_text())
    if set(config) - {'applications'}:
        raise ValueError('Only [applications] configuration is supported in this POC.')
    applications = config.get('applications', {})
    for name, policy in applications.items():
        if name != 'gaze':
            raise ValueError(f'No reviewed adapter is installed for {name!r}.')
        if policy not in POLICIES:
            raise ValueError(f'Unknown policy for {name}: {policy!r}')
    return applications


def selection(policy, upstream, cached=None):
    """Pure policy decision; dependency semantics remain in libalpm."""
    if policy != 'source' and upstream['compatible']:
        return 'publisher-binary'
    if policy == 'upstream-only':
        return 'blocked'
    if cached and cached['compatible']:
        return 'cached-source'
    return 'build-source'


def discover(text, configured):
    names = sorted(n for n in gaze.NAMES.values()
                   if re.search(r'(?<![\w@.+-])' + re.escape(n) + r'(?![\w@.+-])', text))
    if not names:
        return []
    return [{'application': 'gaze', 'packages_seen': names,
             'tracked': 'gaze' in configured,
             'suggested_policy': 'prefer-upstream',
             'action': 'Already tracked' if 'gaze' in configured else 'Run: omarchy-update-compat track gaze'}]


class Coordinator:
    def __init__(self, config, state, cache, pacman_config='/etc/pacman.conf',
                 source_db='/var/lib/pacman', offline=False):
        self.applications = configuration(config)
        self.state, self.cache = Path(state).resolve(), Path(cache).resolve()
        self.pacman_config, self.source_db = pacman_config, Path(source_db)
        self.offline = offline
        self.state.mkdir(parents=True, exist_ok=True)
        self.cache.mkdir(parents=True, exist_ok=True)

    def run(self, stage=False, refresh=False):
        with (self.state / 'coordinator.lock').open('a') as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise RuntimeError('Another compatibility coordinator is running.') from exc
            try:
                return self._run(stage, refresh)
            except (RuntimeError, OSError, ValueError) as error:
                failure = {'schema_version': 1, 'status': 'failed', 'phase': 'stage' if stage else 'plan',
                           'reason': str(error), 'host_packages_changed': False}
                if hasattr(self, 'work'):
                    failure['run_directory'] = str(self.work)
                    native.json_write(self.work / 'failure.json', failure)
                native.json_write(self.state / 'latest.json', failure)
                raise

    def _run(self, stage, refresh):
        ident = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S') + '-' + uuid.uuid4().hex[:8]
        work = self.state / 'runs' / ident
        work.mkdir(parents=True)
        self.work = work
        settings = native.effective_config(self.pacman_config)
        snapshot = native.snapshot(self.source_db, settings, work / 'database', refresh)
        installed = native.inventory(work / 'database')
        native_result = native.prepare(work / 'database', settings, [], self.cache / 'native')
        plan = {'schema_version': 1, 'run_id': ident, 'phase': 'staged' if stage else 'planned',
                'snapshot': snapshot, 'applications': [], 'native_baseline': native_result,
                'validation_scope': 'package dependency preparation; no transaction is committed',
                'host_packages_changed': False, 'status': 'ready' if native_result['compatible'] else 'blocked'}
        all_candidates = []
        for application, policy in self.applications.items():
            managed_names = set(gaze.NAMES.values())
            if not managed_names.intersection(installed):
                plan['applications'].append({'application': application, 'action': 'not-installed',
                                             'reason': 'Tracking does not authorize installing a new application.'})
                continue
            if not managed_names.issubset(installed):
                raise RuntimeError('The Gaze POC requires its daemon, GUI and Omarchy packages as one managed group.')
            ignored = [name for name in sorted(managed_names)
                       if any(fnmatchcase(name, pattern) for pattern in settings['ignore'])
                       or set(installed[name]['groups']).intersection(settings['ignore_groups'])]
            if ignored:
                plan['applications'].append({'application': application, 'action': 'blocked',
                                             'reason': 'Managed packages are pinned by pacman configuration: ' + ', '.join(ignored)})
                plan['status'] = 'blocked'
                continue
            release, raw_upstream = gaze.publisher(self.cache, settings['architecture'], self.offline)
            upstream = gaze.wrap(raw_upstream, 'publisher-binary', self.cache / 'wrappers')
            result = native.prepare(work / 'database', settings, upstream, self.cache / 'native')
            # A managed fallback may not hide unrelated native package conflicts.
            unmanaged = [m for m in result.get('missing', []) if m['package'] not in managed_names]
            if unmanaged or (not result['compatible'] and not result.get('missing')):
                action = 'blocked'
                cached, cached_result = None, None
            else:
                cached, cached_result = self._cached_source(release, work, settings)
                action = selection(policy, result, cached_result)
            previous_local = any('omarchy-compat-gaze-local-source' in info['provides']
                                 for name, info in installed.items() if name in managed_names)
            item = {'application': application, 'policy': policy, 'release': release,
                    'action': action, 'upstream_preparation': result,
                    'handoff_to_upstream': previous_local and action == 'publisher-binary',
                    'reason': self._reason(action, result)}
            chosen = upstream if action == 'publisher-binary' else cached
            if action == 'build-source' and stage:
                print(f'Building Gaze {release["version"]} for the target snapshot; log: {work / "source-build.log"}', file=sys.stderr, flush=True)
                raw_source, provenance = gaze.source_build(release, snapshot['target'], self.cache, work / 'source-build.log')
                chosen = gaze.wrap(raw_source, 'local-source', self.cache / 'wrappers')
                item['source_provenance'] = provenance
            if chosen:
                item['candidates'] = [{'path': str(p), 'sha256': native.digest(p),
                                       'package': gaze.metadata(p)['pkgname'][0],
                                       'version': gaze.metadata(p)['pkgver'][0]} for p in chosen]
                all_candidates.extend(chosen)
            if action in ('blocked', 'build-source'):
                plan['status'] = 'blocked' if action == 'blocked' else 'build-required'
            plan['applications'].append(item)
        if all_candidates:
            final = native.prepare(work / 'database', settings, all_candidates, self.cache / 'native')
            plan['native_final'] = final
            if final['compatible'] and not any(a['action'] == 'blocked' for a in plan['applications']):
                plan['status'] = 'ready'
                for item in plan['applications']:
                    if item['action'] == 'build-source' and item.get('source_provenance'):
                        self._save_source(item['release'], [Path(c['path']) for c in item['candidates']],
                                          item['source_provenance'])
            elif not final['compatible']:
                plan['status'] = 'blocked'
        # Reject concurrent host changes after slow network/build work.
        if native.local_fingerprint(self.source_db) != snapshot['installed_fingerprint']:
            raise RuntimeError('Installed packages changed while planning; retry before staging.')
        if stage and plan['status'] == 'ready' and all_candidates:
            plan['bundle'] = self._bundle(work, snapshot, all_candidates, plan)
        native.json_write(work / 'plan.json', plan)
        native.json_write(self.state / 'latest.json', plan)
        plan['plan_file'] = str(work / 'plan.json')
        return plan

    def _cached_source(self, release, work, settings):
        index = self.cache / 'source-index' / f'{release["tag"]}.json'
        if index.exists():
            record = json.loads(index.read_text())
            candidates = [Path(a['path']) for a in record['candidates']]
            if all(p.exists() and native.digest(p) == a['sha256'] for p, a in zip(candidates, record['candidates'])):
                result = native.prepare(work / 'database', settings, candidates, self.cache / 'native')
                return candidates, result
        return None, None

    def _save_source(self, release, candidates, provenance):
        native.json_write(self.cache / 'source-index' / f'{release["tag"]}.json', {
            'release': release, 'provenance': provenance,
            'candidates': [{'path': str(p), 'sha256': native.digest(p)} for p in candidates]})

    @staticmethod
    def _reason(action, result):
        if action == 'publisher-binary': return 'Publisher packages satisfy the complete target transaction.'
        if action == 'cached-source': return 'A previous local build still satisfies the target transaction; no rebuild is needed.'
        if action == 'build-source':
            if result['compatible']: return 'The source policy selects a build even though publisher packages fit.'
            return 'Publisher binaries do not fit this snapshot; an approved source recipe is available.'
        return 'No permitted candidate satisfies this transaction; dependency enforcement is preserved.'

    def _bundle(self, work, snapshot, candidates, plan):
        repo = work / 'repository'
        repo.mkdir()
        archives = []
        for candidate in candidates:
            dest = repo / candidate.name
            shutil.copyfile(candidate, dest)
            archives.append({'filename': dest.name, 'sha256': native.digest(dest),
                             'package': gaze.metadata(dest)['pkgname'][0],
                             'version': gaze.metadata(dest)['pkgver'][0]})
        native.run(['repo-add', repo / 'omarchy-compat.db.tar.gz',
                    *[repo / a['filename'] for a in archives]])
        bundle = {'schema_version': 1, 'repository': str(repo),
                  'installed_fingerprint': snapshot['installed_fingerprint'],
                  'target': snapshot['target'], 'target_fingerprint': snapshot['target_fingerprint'],
                  'metadata_refreshed': snapshot['metadata_refreshed'],
                  'packages': archives, 'targets': [a['package'] for a in archives],
                  'origins': {a['application']: a['action'] for a in plan['applications']},
                  'validation': plan.get('native_final', plan['native_baseline']),
                  'host_installation_authorized_by_this_command': False}
        native.json_write(work / 'transaction.json', bundle)
        return str(work / 'transaction.json')
