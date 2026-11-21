# Omarchy update compatibility POC

An opt-in coordinator for a tracked application's compatibility across an Arch/Omarchy package update. Configuration is deliberately small:

```toml
[applications]
gaze = "prefer-upstream"
```

The reproduced edge case, recommended native integration boundary and validation are submitted upstream in [omacom/omarchy#19665](https://github.com/omacom/omarchy/pull/19665). The application build change is [GunduLabs/gaze#1027](https://github.com/GunduLabs/gaze/pull/1027).

The coordinator prepares the complete target transaction with **libalpm**, using a private copy of pacman's databases. It selects compatible Gaze publisher packages first, a previously built compatible source package second, and a fresh source build when necessary. A compatible publisher package replaces a local source build on a subsequent request, including when the publisher's packaging revision is lower. Nothing is installed on the host by the planning or staging commands.

## Local use

Requirements: Arch Linux, Python 3.14 (including zstd tar support), pacman/libalpm headers, a C compiler, makepkg, repo-add, fakeroot, Git, and rootless Podman for source builds. The POC uses the active pacman repository configuration; it does not switch OS channels or guess a target from an Omarchy version string.

Install the unprivileged tool and optionally import a successful build cache:

```bash
python scripts/install-local.py --cache-from .cache
omarchy-update-compat track gaze
omarchy-update-compat plan --refresh
omarchy-update-compat stage --refresh
omarchy-update-compat status
```

`track` creates `~/.config/omarchy/update-compat.toml`. The installed command is in `~/.local/bin`, its code in `~/.local/lib/omarchy-compat-poc`, state in `~/.local/state/omarchy/update-compat`, and build cache in `~/.cache/omarchy/update-compat`. Re-running the installer without `--cache-from` updates the tool. It refuses to overwrite an unrelated command or installation directory. It does not alter Omarchy's installed commands, configure a background job, or edit `/etc/pacman.conf`.

Before installation, run the same commands using `bin/omarchy-update-compat --config examples/compat.toml --state-dir .state --cache-dir .cache ...`. Global arguments precede the subcommand. `--offline` uses cached, checksum-verified publisher assets; `--refresh` still contacts the configured pacman repositories. Without `--refresh`, planning uses the host's existing sync metadata.

To inspect a failed update transcript:

```bash
omarchy-update-compat discover /tmp/omarchy-update.log
```

Discovery suggests a known adapter and a tracking command. It never executes log content, downloads a recipe from a log, or automatically approves a newly discovered application.

Policies are `prefer-upstream`, `source` (select source even when publisher packages fit), and `upstream-only` (stop rather than build). Only the reviewed Gaze adapter is supported. Tracking an absent application does not install it. This POC manages the already-installed `gaze-bin`, `gaze-gui-bin`, and `gaze-omarchy-bin` group together. A managed package pinned through IgnorePkg or IgnoreGroup stops planning before selecting an explicit replacement.

## What runs

1. Read the active repository order, servers, architecture, ignored packages/groups, installed package inventory and sync database hashes. Refresh only a private database when requested, using the checkupdates/fakeroot technique.
2. Ask libalpm to prepare the complete system upgrade. Test publisher packages against that same transaction, preserving their OpenCV ABI bounds. Unrelated dependency blockers stop the plan.
3. If required, build the selected Gaze release in a rootless Arch container against the target's Arch mirror. The current source builder supports **x86_64 only**; aarch64 source building stops with an explicit unsupported-backend error.
4. Use makepkg to retain the installed AUR `*-bin` package identities, original payload, install script, backup declarations and ABI bounds. Source builds receive a local packaging revision and a provenance marker. This avoids the unrelated AUR package named `gaze`.
5. Re-prepare all selected candidates together with libalpm. Stage archives and a temporary repository using repo-add, and save a transaction manifest, hashes and build provenance. Reuse a checksum-valid cached build if libalpm still accepts it, including after an unrelated repository revision.

Here, “upstream” means **GunduLabs' published Gaze release**, verified against the digests in GitHub's release metadata. These are wrapped into the existing AUR package identities. They are not Arch official repository packages, and the wrapper is a locally produced package. A general repository/AUR provider backend is a later contribution path.

The source build for Gaze 0.5.2 needs one explicit build-only backport: its original ONNX Runtime tarball URL returns 404. The adapter copies the already-reviewed upstream NuGet download script, records its SHA-256 and upstream commit, and rejects other tracked checkout modifications. It records the exact source commit, builder recipe, container image ID and package hashes. This is provenance, not a claim of byte-for-byte reproducible builds.

## Transaction boundary

Exit codes are 0 (ready), 10 (source build required), 20 (blocked), and 2 (operational failure). `stage --json` prints a machine-readable plan, with build progress on stderr. A failed run saves a failure record rather than leaving the previous successful plan as the latest status.

`verify PATH/transaction.json` checks package identities/hashes, repository configuration and metadata, installed package state, then prepares the candidates again with libalpm. For a refreshed plan, verification refreshes another private database and compares it to the staged target. This permits a legitimate target newer than the host's sync cache without changing that cache. A changed target stops the handoff and requires replanning. Plans made without refresh are verified against the host's cached metadata.

`transaction-args` exposes an experimental NUL-delimited argument contract for Omarchy's existing pacman helper. **It refuses to run outside a container.** The container bridge creates a transient local repository with local unsigned-package policy scoped to that repository and its temporary configuration. It never writes a permanent source or IgnorePkg entry. Pacman still performs its normal signature, dependency, file-conflict and transaction checks.

Dependency preparation does not validate physical authentication, camera operation, the live graphical lock, bootability, arbitrary pacman configuration directives or all file conflicts. Native pacman remains responsible for the actual commit. Live integration requires a reviewed root-owned artifact-import/signing boundary, target metadata reconciliation under the package transaction lock, and Omarchy VM acceptance coverage; see [CONTRIBUTING-PLAN.md](CONTRIBUTING-PLAN.md).

## Verification

```bash
python -m unittest discover -s tests -v >test-results.log 2>&1
```

The tests use real libalpm preparation and real native package archives; network and source builds are substituted in coordinator tests. They cover incompatible publisher binaries, a matching local build, joint library/application upgrades, a lower-revision upstream handoff, one build followed by cache reuse, unrelated blockers, changed metadata/pins/installed state, archive tampering, build failure and discovery behavior. The native makepkg wrapper test runs as an unprivileged user.

The recorded local POC also built the actual Gaze 0.5.2 source release for OpenCV 5.0, staged the three matching `*-bin` packages, reused them on a later request, and committed them through the patched Omarchy pacman helper in a disposable rootless Podman container. The smoke script is [tests/container-smoke.sh](tests/container-smoke.sh). It bootstraps a managed Gaze installation, stages a second transaction, commits it, checks pacman's database, resolves runtime/PAM symbols, invokes CLI help/version and the daemon's invalid-option path, and verifies the service unit.

The [validation record](validation/results.json) includes the 28 passing tests, captured smoke and focused Omarchy test logs, exact source build provenance and unchanged host package versions.

This is a package transaction smoke test, not a complete Omarchy OS upgrade or a physical authentication test. It leaves the host's installed packages and authentication configuration unchanged.

### Follow-up deployment on 2026-11-21

The [host deployment record](validation/host-upgrade.json) captures the subsequent live outcome: Gaze was upgraded from 0.3.7 to the source-built 0.5.2 package group, the installed stock Omarchy updater completed successfully, both Gaze doctors reported healthy/ready, and a camera face-verification test passed. Existing enrollment was retained and migrated to owner-bound TPM encryption. The new package defaults were merged while preserving existing settings. Omarchy remained 4.5.0-3, already the latest stable package; OpenCV remained 5.0.0-13.

This deployment used explicit checksum verification, root-owned artifact copies, native `pacman -U`, then the unchanged installed Omarchy updater. The host and target snapshot had the same ABI, so this is evidence for the actual Gaze upgrade path rather than a general automatic cross-ABI commit implementation. The experimental host bridge remains disabled.

The [official-VM upgrade smoke](validation/vm-upgrade.log) completed the stock updater with the built Gaze packages installed and its daemon running. That empty VM has no TPM; its fixture disables template encryption, while the live workstation keeps it enabled. The [full Omarchy test suite](validation/omarchy-full-vm.log) completed 435 test files without failures in a separate official VM; six files had skipped checks. The running and installed host kernels match; a stale reboot marker from before the current boot was cleared.

## Omarchy integration draft

[integration/omarchy-update.patch](integration/omarchy-update.patch) targets the commit in [integration/omarchy-base.txt](integration/omarchy-base.txt). It is a reviewable draft, not applied to the installed updater:

- An explicit `OMARCHY_COMPAT_POC=1` runs staging after keyrings and before the system package transaction.
- The existing `omarchy-update-pacman` helper imports the bundle's temporary repository and explicit managed targets, preserving Omarchy's sudo environment and systemd scope protection. Failed preparation stops before escalation.
- The later AUR pass excludes only this update's managed targets, preventing an incompatible binary from immediately replacing the chosen build. The next plan can return to upstream automatically.

The existing snapshot, migration, restart and boot-verification flow remains the owner of the update. Automatic coordinator activation on hosts remains a separate production contribution.
