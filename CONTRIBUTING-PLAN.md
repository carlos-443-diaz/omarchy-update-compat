# Contribution paths

The demonstrated problem is coordination around pacman's dependency preparation. A foreign application's old ABI bounds can block the system transaction before AUR updates run; conversely, its new publisher binary can require an ABI newer than the selected stable snapshot. A transaction hook cannot repair a dependency failure that happens before hooks run.

[omacom/omarchy#19665](https://github.com/omacom/omarchy/pull/19665) currently contains the reproduction and recommendation. The local implementation adds an optional packaged coordinator and a small native updater contract. Revise that same PR while it remains open, explaining the executable behavior and linking immutable proof. If the documentation lands first, make a linked code follow-up. Keep the complete proposal reviewable before publishing it; distinguish the historical manual host upgrade from the automatic VM result.

## Omarchy integration

The source integration prepares compatibility after keyrings and before system packages, hands approved candidates to the existing protected pacman helper, verifies the installed selection, and excludes managed applications only from that request's later AUR pass. Untracked users retain the existing pipeline. Snapshot, lock, authorization, PID-1 transaction scope, migrations, service restarts and boot checks retain their existing owners.

The package supplies the restricted root-owned artifact importer and pretransaction target guard. It never runs caller-provided configuration or build scripts as root. The compiled libalpm helper must be distributed as a maintained native package: when pacman's library ABI changes, its matching replacement must participate in the same repository update. This is a packaging responsibility, not a reason to bypass the dependency.

The recommended distro contribution is this small preflight/staged-candidate contract plus native packaging and an explicitly reviewed adapter catalog. Gaze-specific source fixes remain upstream with Gaze. The standalone provider currently handles GunduLabs publisher artifacts under the installed AUR identities; it is not a general Arch repository/AUR resolver or an unattended arbitrary-PKGBUILD builder.

## Evidence and scope

[The acceptance record](validation/acceptance-progress.json) and [checklist](ACCEPTANCE.md) distinguish completed and remaining checks. The automatic VM test reproduces the real 0.3.7/publisher-0.5.2 mismatch, performs an uncached bounded source build and guarded deployment through the stock updater, checks the daemon/loader/database, and repeats with no build or reinstall. Native ABI fixtures additionally prove joint library/publisher replacement, target-drift rejection and preserved file-conflict enforcement. Those fixtures are not a physical cross-ABI Gaze runtime test.

Retain failed attempts where they explain the guard or resource choice, without publishing credentials, personal paths, enrollment or camera material. A checksum or local provenance record is not a vulnerability assessment, reproducible-build result, signed attestation or SLSA level.

Before general production promotion, require native architecture build/runtime coverage, locked-desktop authentication and password fallback, reboot/recovery rehearsal, coordinator self-upgrade/ABI-cohort coverage, review of the privileged boundary, and an agreed maintained package/canonical catalog update channel. The current Gaze source backend explicitly stops on aarch64.

## Gaze and packaging

The application build PR is [GunduLabs/gaze#1027](https://github.com/GunduLabs/gaze/pull/1027). On 2026-11-21, Gaze's maintainer added build-container hardening directly to that PR; it remains open. The adapter's 0.5.2 ONNX Runtime backport is separately pinned to already-reviewed upstream source. Prefer deleting that release-specific backport when an approved release includes the fix.

A reviewed catalog update pins new application releases, artifacts, source revisions and recipes. It can then arrive through the coordinator's normal native package update, with no new per-upgrade build approval. The local POC is not yet that maintained distribution channel. A future native-repository provider should take precedence when the distro supplies a compatible package, while retaining the publisher/source fallback for this reproduced case.
