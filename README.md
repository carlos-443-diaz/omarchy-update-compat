# Omarchy update compatibility POC

An opt-in coordinator for an installed application's compatibility across an Arch/Omarchy update. Pacman/libalpm still resolves and commits the system transaction. The coordinator selects a reviewed published package, reuses a compatible local build, or builds the approved source against the selected system repositories.

```toml
[applications]
gaze = "prefer-upstream"
```

The reproduced Gaze/OpenCV edge case is [omacom/omarchy#19665](https://github.com/omacom/omarchy/pull/19665). The application build contribution is [GunduLabs/gaze#1027](https://github.com/GunduLabs/gaze/pull/1027). See [ACCEPTANCE.md](ACCEPTANCE.md) for the release checks and their limits.

## Install and use

Build and install the optional native component, then opt into the reviewed Gaze adapter:

```bash
python scripts/build-package.py
sudo pacman -U build/package/omarchy-update-compat-0.2.0-1-x86_64.pkg.tar.zst
omarchy-update-compat track gaze
omarchy-update-compat upgrade
```

The upgrade command runs the installed stock Omarchy updater, retaining its confirmation, authentication, snapshot, package guard, systemd scope, migrations and boot checks. The packaged wrappers insert compatibility preparation before the main package transaction; they do not edit installed Omarchy scripts. The proposed native Omarchy integration calls the same contract directly. No background service, permanent repository, permanent IgnorePkg entry or passwordless sudo rule is installed.

The old experimental unprivileged installer in `scripts/install-local.py` installs a planning-only snapshot. If previously used, remove its task-owned `~/.local/bin/omarchy-update-compat` symlink after installing the native component so it cannot shadow `/usr/bin/omarchy-update-compat`.

Requirements are Arch Linux, Python 3.14, pacman/libalpm, Git, fakeroot and sudo. Native component construction needs makepkg and a C compiler. Source builds additionally need rootless Podman with delegated cgroup v2 controllers and at least 15 GiB free disk space. The reviewed Gaze provider currently supports **native x86_64 only**; aarch64 stops explicitly rather than guessing a binary or cross-build recipe.

Tracking writes `~/.config/omarchy/update-compat.toml`. State is under `~/.local/state/omarchy/update-compat`, and cache under `~/.cache/omarchy/update-compat`. Removing Gaze from the tracking file disables its management; it leaves the installed package group intact. Retained diagnostic runs and builder images can be removed explicitly when no update is running; the tool never performs a broad container-engine prune.

## What the user sees

```text
Gaze  CHECKING   Installed 0.3.7-1; checking the target system.
Gaze  PLAN       Publisher binaries do not fit this snapshot; an approved source recipe is available.
Gaze  BUILDING   Compiling 0.5.2 for this system. Detailed output is saved in the run log.
Gaze  READY      Compatible package group selected and checked.
Gaze  INSTALLING With the system update...
Gaze  DONE       Installed 0.5.2 — local build.
```

A later request reports `KEEP` when the selected build is already installed. It does not rebuild or reinstall it. A compatible reviewed publisher package takes precedence on the next request, including a lower packaging revision of the same application version. `BLOCKED` explains preparation failure; it does not report a successful installation. The installed package identities/dependencies are checked before `DONE`.

There is no additional routine build-confirmation question after tracking. Existing Omarchy confirmation and sudo authentication remain. An unknown application, new unreviewed release, invalid recipe, missing resource control, pin, or unresolved conflict stops preparation. It never turns a failed update log into executable code.

## Policy and inspection

Policies are `prefer-upstream`, `source` (prefer an approved source build even when the publisher fits), and `upstream-only` (stop rather than build). The current adapter manages the already-installed `gaze-bin`, `gaze-gui-bin` and `gaze-omarchy-bin` group together; tracking does not authorize introducing an absent application. Existing IgnorePkg/IgnoreGroup settings are respected. A newer manually installed application is not silently downgraded to an older reviewed release.

```bash
omarchy-update-compat plan --refresh
omarchy-update-compat stage --refresh
omarchy-update-compat status
omarchy-update-compat discover /tmp/omarchy-update.log
```

Planning and staging do not change host packages. They copy the native database, optionally refresh that private copy, and prepare the complete target transaction with libalpm. Unrelated dependency failures remain blockers. A successful stage saves a restricted candidate manifest and hashes. Global arguments precede the subcommand; `--offline` uses cached publisher archives but does not disable the explicit metadata refresh or a required source/dependency fetch. Exit codes are 0 (ready), 10 (build required), 20 (blocked plan), 2 (operational failure), and 130 (interrupted request).

## Trust and bounded execution

The root-owned [reviewed catalog](policies/gaze.json) pins the Gaze release, source commit, Cargo.lock, publisher archive checksums and build recipes. It does not approve whatever a mutable latest-release API returns. A new release requires a reviewed component/catalog update; a compatible publisher artifact already in the catalog is selected automatically. Here “published” means GunduLabs' release wrapped under the installed AUR package identities. It does not mean an Arch official package. A general native-repository/AUR provider is a further contribution path.

Source builds use a pinned Arch image, signed target-repository toolchain packages, pinned nfpm and ONNX Runtime artifacts, and `cargo --locked`. Gaze 0.5.2 needs the recorded upstream ONNX Runtime download backport because its original tarball endpoint is unavailable. A fresh tracked source archive avoids ignored or modified checkout files. Dependency fetching runs separately; compilation has no network. No workstation home, credentials, devices or engine socket are mounted into the builder, and input mounts are read-only.

The overall build has a 30-minute budget. Build containers enforce 4 GiB memory, two CPUs, 512 PIDs, a 4 GiB workspace and 512 MiB temporary filesystem. They drop capabilities, deny privilege acquisition, use a read-only root filesystem, and verify the cgroup limits at entry. Container lifetime is also capped independently of the coordinator. Output archives are bounded to 256 MiB, dependency archives to 1 GiB, and the combined build log to 8 MiB. Cancellation stops owned child processes and named build containers; failure does not retry indefinitely. These build deadlines do not kill native pacman during a host commit.

The finite signed-package environment preparation uses the same CPU/memory bounds and command deadlines, with a larger 4096-process `nproc` ceiling: that limit counts the whole real UID and is not a per-container PID limit. A 512-process ceiling there exhausted GPG verification resources during testing; signature enforcement correctly stopped that attempt. Dependency and compilation jobs use their separate verified cgroup PID limit.

The installed isolated-Python privileged helper accepts only the tracked Gaze group and the existing full-upgrade flags. It rejects arbitrary configuration, root/database overrides, dependency bypasses, symlinks and inputs writable by other users. It copies archives into protected root-owned storage before checking identities, hashes, architecture and current approval. It refreshes and prepares again, then a native pretransaction hook rechecks installed state, policy and the target repositories while pacman holds its transaction lock. Regular repository signature policy remains intact; unsigned locally built artifacts are accepted only in the temporary local repository for that approved transaction.

The local administrator and their container engine remain trusted. Provenance is a diagnostic record, not a signed attestation or a security certification. Artifact hashes do not prove the absence of vulnerabilities. Tests and advisory review support the claim only for the revisions and environments recorded.

The compiled native helper depends on pacman/libalpm's ABI. Production distribution needs a maintained coordinator package rebuilt alongside that native library, as other Arch packages are. The local POC does not promise to rebuild its own helper across a future library ABI change; the matching native package and self-upgrade acceptance are part of the packaging contribution path.

## Evidence and remaining acceptance

```bash
python -m unittest discover -s tests -v >test-results.log 2>&1
```

Tests use real native archives and libalpm preparation; provider network/build work is substituted in unit tests. Root import tests run in a disposable container. [tests/container-handoff.py](tests/container-handoff.py) performs an actual native transaction using small ABI fixtures, including target-drift rejection before commit and a joint library upgrade/publisher handoff. [tests/vm-automatic-upgrade.sh](tests/vm-automatic-upgrade.sh) exercises the uncached build/deploy/repeat path in a disposable official image. Neither substitutes for physical camera, TPM, locked-desktop, password fallback or boot recovery acceptance.

Historical evidence is retained in [validation/results.json](validation/results.json) and [validation/host-upgrade.json](validation/host-upgrade.json). The workstation's Gaze 0.3.7 to source-built 0.5.2 deployment was successful, its stock Omarchy update completed, both Gaze doctors passed, and camera verification succeeded with enrollment and TPM encryption retained. Omarchy was already the latest stable 4.5.0-3, with OpenCV 5.0.0-13. That deployment was manual.

The separate [acceptance record](validation/acceptance-progress.json) records the later automatic cold-build/deployment/repeat VM proof, focused tests, native transaction guards, RustSec review and full Omarchy regressions. It identifies the tested snapshots and subsequent focused checks rather than claiming every test exercised every revision. Native aarch64, locked-desktop authentication, boot recovery and coordinator ABI/self-upgrade acceptance remain unclaimed.

The [local activation record](validation/local-automation.json) confirms the packaged implementation was installed and `omarchy-update-compat upgrade` completed on the workstation. It reported `KEEP`, preserved the installed Gaze build without recompilation or reinstallation, and passed both Gaze doctors and native database checks. Use this entry point on the installed stock release; ordinary `omarchy update` gains the same preflight when the proposed native integration is packaged upstream.
