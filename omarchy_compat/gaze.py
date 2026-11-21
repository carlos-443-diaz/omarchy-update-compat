# SPDX-FileCopyrightText: 2026 Carlos Diaz
# SPDX-License-Identifier: GPL-3.0-or-later
"""Curated Gaze provider. Publisher code is never inferred from a failure log."""
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import tempfile
import time
import uuid
import tarfile
import urllib.request
from urllib.parse import urlsplit

from .native import ROOT, digest, json_write, run
from . import limits

NAMES = {'gaze': 'gaze-bin', 'gaze-gui': 'gaze-gui-bin', 'gaze-omarchy': 'gaze-omarchy-bin'}
API = 'https://api.github.com/repos/GunduLabs/gaze/releases/latest'
SOURCE = 'https://github.com/GunduLabs/gaze.git'


def metadata(archive):
    content = None
    with tarfile.open(archive, mode='r|*') as tar:
        expanded = 0
        for count, member in enumerate(tar, 1):
            expanded += member.size
            if count > 10000 or expanded > 512 * 1024**2:
                raise RuntimeError('Package exceeds the supported archive budget.')
            if member.name == '.PKGINFO':
                if not member.isfile() or member.size > 1048576:
                    raise RuntimeError('Invalid or oversized package metadata.')
                content = tar.extractfile(member).read(1048577)
                break
    if content is None:
        raise RuntimeError('Package metadata is missing.')
    if len(content) > 1048576:
        raise RuntimeError('Package metadata exceeds the supported limit.')
    content = content.decode()
    result = {}
    for line in content.splitlines():
        if ' = ' in line:
            key, value = line.split(' = ', 1)
            result.setdefault(key, []).append(value)
    return result


def catalog():
    path = ROOT / 'policies/gaze.json'
    policy = json.loads(path.read_text())
    if policy.get('schema_version') != 1:
        raise RuntimeError('Unsupported installed Gaze trust policy.')
    for name, checksum in policy['recipe_sha256'].items():
        if digest(ROOT / name) != checksum:
            raise RuntimeError('Installed build recipe differs from the reviewed trust policy.')
    return policy, digest(path)


def fetch(url, destination, expected=None, limit=256 * 1024 * 1024):
    destination = Path(destination)
    if expected and destination.is_file() and digest(destination) == expected:
        return destination
    if not url.startswith('https://'):
        raise RuntimeError('Only HTTPS publisher downloads are permitted.')
    destination.parent.mkdir(parents=True, exist_ok=True)
    request = urllib.request.Request(url, headers={'User-Agent': 'omarchy-update-compat'})
    temp = destination.with_suffix('.part')
    deadline = time.monotonic() + 300
    try:
        with urllib.request.urlopen(request, timeout=30) as response, temp.open('wb') as out:
            remaining = limit
            while block := response.read(min(65536, remaining + 1)):
                remaining -= len(block)
                if remaining < 0 or time.monotonic() > deadline:
                    raise RuntimeError('Publisher download exceeded its size or time budget.')
                out.write(block)
        if expected and digest(temp) != expected:
            raise RuntimeError(f'Publisher checksum mismatch: {destination.name}')
        os.replace(temp, destination)
    finally:
        temp.unlink(missing_ok=True)
    return destination


def publisher(cache, architecture, offline=False):
    # Releases become eligible through a reviewed component/catalog update, not
    # through a mutable latest-release API or an error-log suggestion.
    policy, checksum = catalog()
    release = dict(policy['release'], catalog_sha256=checksum,
                   security_profile=policy['security_profile'])
    archives = []
    for name in NAMES:
        filename = f'{name}-{release["version"]}-1-{architecture}.pkg.tar.zst'
        asset = policy['assets'].get(filename)
        if not asset:
            raise RuntimeError(f'No reviewed {architecture} publisher artifact is installed for {name}.')
        path = Path(cache) / 'publisher' / release['tag'] / filename
        if offline:
            if not path.is_file() or digest(path) != asset['sha256']:
                raise RuntimeError(f'Missing or invalid cached archive: {filename}')
        else:
            fetch(asset['url'], path, asset['sha256'])
        info = metadata(path)
        if info.get('pkgname') != [name] or info.get('arch') != [architecture]:
            raise RuntimeError('Publisher archive identity differs from the approved asset.')
        archives.append(path)
    return release, archives


def wrap(archives, origin, destination):
    """Native makepkg wrappers retain the AUR names and the publisher's ABI bounds."""
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    built = []
    for archive in archives:
        archive = Path(archive)
        info = metadata(archive)
        name = info['pkgname'][0]
        if name not in NAMES:
            raise RuntimeError(f'Unexpected managed package: {name}')
        version, release = info['pkgver'][0].rsplit('-', 1)
        if not re.fullmatch(r'[0-9]+\.[0-9]+\.[0-9]+', version) or not re.fullmatch(r'[0-9]+(?:\.[0-9]+)*', release):
            raise RuntimeError('Unsupported package version format.')
        if origin == 'local-source':
            release += '.1'
        key = hashlib.sha256(f'{digest(archive)}:{origin}:wrapper-v1'.encode()).hexdigest()[:24]
        work = destination / key
        work.mkdir(exist_ok=True)
        stamp = work / 'result.json'
        if stamp.exists():
            cached = json.loads(stamp.read_text())
            candidate = work / cached['filename']
            if candidate.exists() and digest(candidate) == cached['sha256']:
                built.append(candidate); continue
        payload = work / 'payload'
        if payload.exists():
            shutil.rmtree(payload)
        payload.mkdir()
        with tarfile.open(archive, mode='r|*') as tar:
            expanded = 0
            for count, member in enumerate(tar, 1):
                expanded += member.size
                if count > 10000 or expanded > 512 * 1024**2:
                    raise RuntimeError('Package exceeds the supported archive budget.')
                # Reject expansion bombs before consuming member data; the data
                # filter rejects traversal and links outside this tree.
                tar.extract(member, payload, filter='data')
        install = payload / '.INSTALL'
        has_install = install.exists()
        if has_install:
            shutil.copyfile(install, work / 'package.install')
        for path in list(payload.iterdir()):
            if path.name.startswith('.'):
                if path.is_dir(): shutil.rmtree(path)
                else: path.unlink()
        def array(values):
            return '(' + ' '.join(shlex.quote(v) for v in values) + ')'
        dependencies = [NAMES.get(d, d) for d in info.get('depend', [])]
        provides = info.get('provides', []) + [f'{name}={version}',
                    f'omarchy-compat-gaze-{origin}']
        lines = [f'pkgname={shlex.quote(NAMES[name])}', f'pkgver={shlex.quote(version)}',
                 f'pkgrel={release}', f'arch={array(info["arch"])}',
                 f'pkgdesc={shlex.quote(info.get("pkgdesc", [name])[0])}',
                 f'license={array(info.get("license", ["GPL-3.0-or-later"]))}',
                 f'url={shlex.quote("https://gaze.gundulabs.com")}',
                 f'depends={array(dependencies)}', f'provides={array(provides)}',
                 f'conflicts={array(info.get("conflict", []) + [name])}',
                 f'backup={array(info.get("backup", []))}', 'options=(!strip !debug)',
                 'package() { cp -a "$startdir/payload/." "$pkgdir/"; }']
        if has_install: lines.insert(0, 'install=package.install')
        (work / 'PKGBUILD').write_text('\n'.join(lines) + '\n')
        env = os.environ.copy()
        env['PACKAGER'] = f'Omarchy compatibility POC ({origin})'
        result = run(['makepkg', '--nodeps', '--force', '--noconfirm'], cwd=work, env=env)
        (work / 'makepkg.log').write_text(result.stdout + result.stderr)
        outputs = list(work.glob(f'{NAMES[name]}-{version}-{release}-*.pkg.tar.*'))
        outputs = [p for p in outputs if not p.name.endswith('.sig')]
        if len(outputs) != 1:
            raise RuntimeError('makepkg did not produce exactly one managed package.')
        json_write(stamp, {'filename': outputs[0].name, 'sha256': digest(outputs[0])})
        built.append(outputs[0])
    return built


def source_build(release, target, cache, log_path):
    policy, policy_hash = catalog()
    if release.get('catalog_sha256') != policy_hash or release['tag'] != policy['release']['tag']:
        raise RuntimeError('Source build is not covered by the installed reviewed policy.')
    if target['architecture'] != 'x86_64':
        raise RuntimeError('The source builder requires native x86_64; a native aarch64 backend is not validated.')
    servers = target['servers'].get('extra', [])
    suffix = '/extra/os/x86_64'
    if len(servers) != 1 or not servers[0].endswith(suffix):
        raise RuntimeError('The source builder needs one target Arch mirror.')
    mirror = servers[0][:-len(suffix)]
    address = urlsplit(mirror)
    if (address.scheme != 'https' or not address.hostname or address.username is not None
            or address.password is not None or address.query or address.fragment):
        raise RuntimeError('The source builder requires a credential-free HTTPS mirror URL.')
    if run(['podman', 'info', '--format', '{{.Host.Security.Rootless}}']).stdout.strip() != 'true':
        raise RuntimeError('Source builds require rootless Podman.')
    cache = Path(cache)
    if shutil.disk_usage(cache).free < 15 * 1024**3:
        raise RuntimeError('Source build needs at least 15 GiB of free space; free space and retry.')
    identity = {'source_commit': policy['release']['source_commit'],
                'target': hashlib.sha256(json.dumps(target, sort_keys=True).encode()).hexdigest(),
                'catalog_sha256': policy_hash, 'security_profile': policy['security_profile'],
                'recipe_sha256': policy['recipe_sha256']}
    key = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()[:24]
    output = cache / 'builds' / key
    output.mkdir(parents=True, exist_ok=True)
    stamp = output / 'provenance.json'
    if stamp.exists():
        record = json.loads(stamp.read_text())
        entries = record.get('archives', [])
        archives = [output / p['filename'] for p in entries]
        if (len(entries) == 3 and all(record.get(k) == v for k, v in identity.items())
                and all(p.is_file() and digest(p) == r['sha256'] for p, r in zip(archives, entries))):
            return archives, {**record, 'cache_hit': True}
    deadline = time.monotonic() + limits.BUILD_SECONDS
    def phase(arguments, **kwargs):
        limits.command(arguments, log_path, timeout=deadline - time.monotonic(), **kwargs)
    Path(log_path).write_text('Reviewed source build; dependencies fetched before offline compilation.\n')
    checkout = cache / 'source' / 'objects'
    if not (checkout / 'HEAD').exists():
        checkout.mkdir(parents=True, exist_ok=True)
        run(['git', 'init', '--bare', checkout])
    commit = identity['source_commit']
    found = run(['git', '-C', checkout, 'cat-file', '-t', commit], accepted=(0, 128))
    if found.returncode:
        phase(['git', '-C', checkout, 'fetch', '--depth=1', SOURCE, commit])
    if run(['git', '-C', checkout, 'rev-parse', commit]).stdout.strip() != commit:
        raise RuntimeError('Fetched source differs from the reviewed commit.')
    image = f'localhost/omarchy-compat-gaze:{key}'
    jobs = []
    with tempfile.TemporaryDirectory(prefix='build-', dir=output) as directory:
        work = Path(directory)
        tree = work / 'source'; tree.mkdir()
        phase(['git', '-C', checkout, 'archive', commit], output=work / 'original.tar', output_limit=128 * 1024**2)
        with tarfile.open(work / 'original.tar') as tar:
            tar.extractall(tree, filter='data')
        if digest(tree / 'Cargo.lock') != policy['release']['cargo_lock_sha256']:
            raise RuntimeError('Cargo.lock differs from the reviewed source.')
        shutil.copyfile(ROOT / 'builders/gaze-fetch-ort-0.5.2.sh', tree / 'scripts/fetch-ort.sh')
        with tarfile.open(work / 'source.tar', 'w') as tar:
            for entry in tree.iterdir():
                tar.add(entry, arcname=entry.name)
        # RLIMIT_NPROC counts the whole real UID, including the desktop and
        # GPG verification children. It is not the per-container PID controller.
        # Keep a larger ceiling for the finite, signed environment preparation;
        # dependency/source containers enforce their separate cgroup PID budget.
        phase(['podman', 'build', '--http-proxy=false', '--memory=4g', '--memory-swap=5g',
               '--cpu-period=100000', '--cpu-quota=200000', '--ulimit=nproc=4096:4096',
               '--build-arg', f'ARCH_MIRROR={mirror}', '--build-arg', f'TARGET_SNAPSHOT={identity["target"]}',
               '-t', image, '-f', ROOT / 'builders/Containerfile', ROOT / 'builders'])
        image_id = run(['podman', 'image', 'inspect', '--format', '{{.Id}}', image]).stdout.strip()
        # A stable mirror URL can advance while the environment is being made.
        # Cache-busting alone is not proof that its installed headers match the
        # planned target. Reject a different native repository state before any
        # source build; the root handoff rechecks the full target again later.
        inspected = run(['podman', 'run', '--rm', '--network=none', '--read-only',
                         '--memory=64m', '--pids-limit=32', '--timeout=30', '--cap-drop=all',
                         '--security-opt=no-new-privileges', '--entrypoint=/usr/bin/sha256sum',
                         image_id, '/var/lib/pacman/sync/core.db', '/var/lib/pacman/sync/extra.db']).stdout
        verify_build_target(inspected, target)
        def container(mode):
            name = f'omarchy-compat-{uuid.uuid4().hex}'
            jobs.append(name)
            args = ['podman', 'run', '--rm', '--init', '--name', name, '--log-driver=none', '--http-proxy=false',
                    '--timeout', str(max(1, int(deadline - time.monotonic()))),
                    '--read-only', '--read-only-tmpfs=false',
                    '--tmpfs', '/work:rw,size=4g,nodev,nosuid', '--tmpfs', '/tmp:rw,size=512m,nodev,nosuid',
                    '--memory=4g', '--memory-swap=5g', '--cpus=2', '--pids-limit=512',
                    '--cap-drop=all', '--security-opt=no-new-privileges',
                    '--volume', f'{work / "source.tar"}:/input/source.tar:ro',
                    '--env', f'VERSION={release["version"]}', '--env', 'PACKAGE_RELEASE=1',
                    '--env', 'RUSTFLAGS=-Cdebuginfo=0']
            if mode == 'compile':
                args += ['--network=none', '--volume', f'{work / "deps.tar"}:/input/deps.tar:ro']
            return [*args, image_id, mode]
        try:
            phase(container('fetch'), output=work / 'deps.tar', output_limit=1024**3)
            phase(container('compile'), output=work / 'packages.tar')
        finally:
            for name in jobs:
                run(['podman', 'rm', '--force', '--ignore', name], accepted=(0, 1), timeout=15)
        expected = {f'{name}-{release["version"]}-1-x86_64.pkg.tar.zst' for name in NAMES}
        with tarfile.open(work / 'packages.tar') as tar:
            members = tar.getmembers()
            if ({m.name for m in members} != expected or len(members) != 3
                    or any(not m.isfile() or m.size > limits.OUTPUT_BYTES for m in members)):
                raise RuntimeError('Build output is not exactly the approved package group.')
            tar.extractall(output, members=members, filter='data')
    archives = [output / name for name in sorted(expected)]
    record = {**identity, 'image': image_id, 'cache_hit': False,
              'elapsed_seconds': round(limits.BUILD_SECONDS - (deadline - time.monotonic()), 2),
              'limits': {'seconds': limits.BUILD_SECONDS, 'memory': '4GiB', 'cpus': 2, 'pids': 512,
                         'workspace': '4GiB tmpfs', 'compile_network': 'none'},
              'archives': [{'filename': p.name, 'sha256': digest(p)} for p in archives]}
    json_write(stamp, record)
    return archives, record


def verify_build_target(checksums, target):
    found = {}
    for line in checksums.splitlines():
        checksum, filename = line.split(maxsplit=1)
        found[Path(filename).stem] = checksum
    if (not {'core', 'extra'}.issubset(target['repositories'])
            or any(found.get(name) != target['repositories'][name] for name in ('core', 'extra'))):
        raise RuntimeError('Build environment repositories differ from the planned system target; replan before building.')
