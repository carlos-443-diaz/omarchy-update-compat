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
import tarfile
import urllib.request

from .native import ROOT, digest, json_write, run

NAMES = {'gaze': 'gaze-bin', 'gaze-gui': 'gaze-gui-bin', 'gaze-omarchy': 'gaze-omarchy-bin'}
API = 'https://api.github.com/repos/GunduLabs/gaze/releases/latest'
SOURCE = 'https://github.com/GunduLabs/gaze.git'


def metadata(archive):
    with tarfile.open(archive) as tar:
        content = tar.extractfile('.PKGINFO').read().decode()
    result = {}
    for line in content.splitlines():
        if ' = ' in line:
            key, value = line.split(' = ', 1)
            result.setdefault(key, []).append(value)
    return result


def fetch(url, destination, expected=None):
    destination = Path(destination)
    if expected and destination.exists() and digest(destination) == expected:
        return destination
    destination.parent.mkdir(parents=True, exist_ok=True)
    request = urllib.request.Request(url, headers={'User-Agent': 'omarchy-compat-poc'})
    temp = destination.with_suffix('.part')
    try:
        with urllib.request.urlopen(request, timeout=60) as response, temp.open('wb') as out:
            shutil.copyfileobj(response, out)
        if expected and digest(temp) != expected:
            raise RuntimeError(f'Publisher checksum mismatch: {destination.name}')
        os.replace(temp, destination)
    finally:
        temp.unlink(missing_ok=True)
    return destination


def publisher(cache, architecture, offline=False):
    cache = Path(cache)
    manifest = cache / 'publisher' / 'release.json'
    if not offline:
        fetch(API, manifest)
    if not manifest.exists():
        raise RuntimeError('No cached publisher release; run once with network access.')
    release = json.loads(manifest.read_text())
    tag = release['tag_name']
    if not re.fullmatch(r'v[0-9]+\.[0-9]+\.[0-9]+', tag):
        raise RuntimeError(f'Unsupported publisher release tag: {tag!r}')
    version = tag[1:]
    archives = []
    for name in NAMES:
        assets = [a for a in release['assets']
                  if a['name'].startswith(f'{name}-{version}-')
                  and a['name'].endswith(f'-{architecture}.pkg.tar.zst')]
        if len(assets) != 1:
            raise RuntimeError(f'No unambiguous {architecture} publisher archive for {name}.')
        asset = assets[0]
        checksum = asset.get('digest', '')
        if not re.fullmatch(r'sha256:[0-9a-f]{64}', checksum):
            raise RuntimeError(f'Publisher digest unavailable for {name}.')
        path = cache / 'publisher' / tag / asset['name']
        if offline:
            if not path.exists() or digest(path) != checksum[7:]:
                raise RuntimeError(f'Missing or invalid cached archive: {asset["name"]}')
        else:
            fetch(asset['browser_download_url'], path, checksum[7:])
        info = metadata(path)
        if info.get('pkgname') != [name] or info.get('arch') != [architecture]:
            raise RuntimeError('Publisher archive identity differs from the approved asset.')
        archives.append(path)
    return {'tag': tag, 'version': version, 'manifest_sha256': digest(manifest)}, archives


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
        with tarfile.open(archive) as tar:
            # Python's data filter rejects traversal and links outside this tree.
            tar.extractall(payload, filter='data')
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
    if target['architecture'] != 'x86_64':
        raise RuntimeError('The POC source builder is x86_64 only; a native ARM backend is required.')
    servers = target['servers'].get('extra', [])
    suffix = '/extra/os/x86_64'
    if len(servers) != 1 or not servers[0].endswith(suffix):
        raise RuntimeError('The POC needs one target Arch mirror for the source build.')
    mirror = servers[0][:-len(suffix)]
    if not mirror.startswith('https://'):
        raise RuntimeError('The POC source builder requires an HTTPS mirror.')
    cache = Path(cache)
    checkout = cache / 'source' / release['tag']
    checkout.parent.mkdir(parents=True, exist_ok=True)
    if not (checkout / '.git').exists():
        run(['git', 'clone', '--depth', '1', '--branch', release['tag'], SOURCE, checkout])
    commit = run(['git', '-C', checkout, 'rev-parse', 'HEAD']).stdout.strip()
    patches = []
    if release['tag'] == 'v0.5.2':
        # Reviewed upstream build-only fix: NuGet replaced unavailable ORT tarballs.
        backport = ROOT / 'builders' / 'gaze-fetch-ort-0.5.2.sh'
        patches = [{'path': 'scripts/fetch-ort.sh', 'sha256': digest(backport),
                    'upstream_commit': 'd380b9b5dde195d22a8c9ad278b874b87ddac77f'}]
    changes = run(['git', '-C', checkout, 'status', '--porcelain', '--untracked-files=no']).stdout.splitlines()
    for line in changes:
        path = line[3:]
        expected = next((p for p in patches if p['path'] == path), None)
        if not expected or not (checkout / path).is_file() or digest(checkout / path) != expected['sha256']:
            raise RuntimeError('Source checkout has unrecognized modifications; refusing to build.')
    for patch in patches:
        shutil.copyfile(ROOT / 'builders' / 'gaze-fetch-ort-0.5.2.sh', checkout / patch['path'])
    target_key = hashlib.sha256(json.dumps(target, sort_keys=True).encode()).hexdigest()
    identity = {'source_commit': commit, 'target': target_key,
                'builder_recipe': digest(ROOT / 'builders' / 'Containerfile'),
                'builder_entrypoint': digest(ROOT / 'builders' / 'entrypoint.sh'),
                'build_backports': patches}
    key = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()[:24]
    output = cache / 'builds' / key
    output.mkdir(parents=True, exist_ok=True)
    stamp = output / 'provenance.json'
    if stamp.exists():
        record = json.loads(stamp.read_text())
        archives = [output / p['filename'] for p in record['archives']]
        if all(p.exists() and digest(p) == r['sha256'] for p, r in zip(archives, record['archives'])):
            return archives, {**record, 'cache_hit': True}
    image = f'localhost/omarchy-compat-gaze:{target_key[:16]}'
    commands = [
        ['podman', 'build', '--build-arg', f'ARCH_MIRROR={mirror}', '--build-arg',
         f'TARGET_SNAPSHOT={target_key}', '-t', image, '-f', ROOT / 'builders' / 'Containerfile', ROOT / 'builders'],
    ]
    with Path(log_path).open('w') as log:
        for command in commands:
            result = subprocess.run([str(a) for a in command], stdout=log, stderr=subprocess.STDOUT)
            if result.returncode: raise RuntimeError(f'Source environment failed; see {log_path}')
        image_id = run(['podman', 'image', 'inspect', '--format', '{{.Id}}', image]).stdout.strip()
        env = os.environ.copy()
        env.update({'VERSION': release['version'], 'PACKAGE_RELEASE': '1',
                    'CARGO_BUILD_JOBS': '2', 'RUSTFLAGS': '-Cdebuginfo=0'})
        command = ['podman', 'run', '--rm', '-v', f'{checkout}:/work',
                   '-v', 'omarchy-compat-gaze-registry:/root/.cargo/registry',
                   '-v', f'omarchy-compat-gaze-{key}:/work/target',
                   '-e', 'VERSION', '-e', 'PACKAGE_RELEASE', '-e', 'CARGO_BUILD_JOBS', '-e', 'RUSTFLAGS',
                   image_id, 'package', 'archlinux']
        result = subprocess.run(command, env=env, stdout=log, stderr=subprocess.STDOUT)
        if result.returncode: raise RuntimeError(f'Gaze source build failed; see {log_path}')
    archives = []
    for name in NAMES:
        matches = list((checkout / 'dist' / 'packages').glob(f'{name}-{release["version"]}-1-*.pkg.tar.*'))
        if len(matches) != 1: raise RuntimeError(f'Missing source-built archive: {name}')
        dest = output / matches[0].name
        shutil.copyfile(matches[0], dest); archives.append(dest)
    record = {**identity, 'image': image_id, 'cache_hit': False,
              'archives': [{'filename': p.name, 'sha256': digest(p)} for p in archives]}
    json_write(stamp, record)
    return archives, record
