# Contribution paths after the local POC

The demonstrated gap is coordination around pacman's dependency preparation: a foreign application's installed ABI bounds can block an OS update, while a new publisher binary can require a newer ABI than the distro snapshot. A transaction hook cannot build a remedy for a transaction that fails before hooks run. The coordinator belongs before the native transaction; dependency resolution belongs in libalpm.

## 1. Omarchy: smallest useful integration

Start discussion with the reproducible Gaze/OpenCV failure, the passing native preparation and container smoke evidence, and the small [integration draft](integration/omarchy-update.patch). Propose an opt-in application compatibility phase between keyring preparation and the existing system package update. Keep builds unprivileged and outside the package transaction lock. Keep the existing snapshot, systemd scope, migrations and boot checks.

Before submitting executable host integration:

- Agree whether maintainers want a built-in coordinator, an optional packaged companion, or a stable preflight/staged-candidate extension interface. The present draft depends on this separate Python/native tool; it is not a standalone Omarchy patch.
- Define a root-owned artifact-import boundary that validates a restricted manifest, copies artifacts into a directory users cannot modify, verifies identities and signatures, and does not let arbitrary user-supplied pacman configuration run as root. The prototype's user-writable temporary configuration is confined to containers and must not be enabled on hosts as-is.
- Prevent a repository change between final preparation and commit. The POC rechecks a refreshed target in another private database, accepting a target newer than the host's sync cache while rejecting a changed target. Production integration must pin or reconcile that metadata under the transaction lock.
- Support effective pacman configuration beyond the repository/architecture/IgnorePkg/IgnoreGroup subset used here, including repository usage and unusual dependency overrides, or explicitly reject unsupported modes. The POC already stops for pinned managed packages rather than overriding them with explicit replacement targets.
- Add a source-build backend based on Arch devtools clean chroots where appropriate, disk-space/cancellation handling, and rebuild output retention. Podman is the working POC backend.
- Run a full Omarchy VM upgrade through a simulated ABI transition, with ordinary update failure/retry, package conflicts, interrupted builds and upstream handoff. Exercise the later AUR pass and the existing full test suite in that VM.
- Require Gaze's own compatibility doctor and camera/lock authentication acceptance before enabling the replacement login integration on a real desktop. Package dependency compatibility alone is insufficient.

The API should be a staged-candidate manifest and status contract, rather than a dependency on Gaze-specific scripts inside Omarchy. The initial managed catalog should be deliberately small and reviewed. Failure-log discovery should suggest tracking known applications, while build authorization comes from the user's configuration.

## 2. Gaze: keep application fixes with the application

The existing [native Arch container-build PR](https://github.com/GunduLabs/gaze/pull/1027) addresses building against a derivative's repository snapshot. That remains separate from OS update coordination. The POC discovered a second release-specific issue: the 0.5.2 ONNX Runtime tarball endpoint returns 404; upstream main already contains the NuGet replacement used by the adapter. Prefer a released upstream fix so the adapter backport can be deleted.

Useful next Gaze work is release/build metadata describing supported architectures and ABI requirements, and native x86_64/aarch64 package build and runtime jobs. Preserve Gaze's own package smoke, PAM-link and Omarchy compatibility checks. Do not claim ARM support from an x86_64 container build or an ELF/checksum inspection alone.

## 3. Packaging and reusable Arch backend

If the policy is useful beyond Omarchy, package the coordinator as an optional Arch tool and retain makepkg, devtools, repo-add and libalpm as the execution/resolution primitives. Add first-class repository candidates before publisher/AUR/source fallback; the current Gaze adapter wraps publisher archives under the installed AUR identities. Work with existing packaging projects rather than introducing a new resolver or permanent package pin system.

Per-application configuration can stay `gaze = "prefer-upstream"` because the reviewed adapter contains the package mapping, trusted upstream, native source recipe and architecture conditions. Other applications need their own reviewed adapters or a constrained packaging-provider interface. Do not infer arbitrary build instructions from error text.

## POC acceptance and promotion

Completed locally: actual source build on the OpenCV 5.0 snapshot; real libalpm dependency checks; package identity/install-script preservation; source-cache reuse; native fixture preparation of a publisher handoff including a packaging downgrade; stale/tampered bundle rejection; an actual disposable-container pacman transaction through Omarchy's patched helper; a successful stock update in an official Omarchy VM; all 435 Omarchy test files completed without failures (six with skipped checks); and explicit live deployment followed by successful camera verification and both Gaze doctors. See [the deployment record](validation/host-upgrade.json).

Still required for automatic production integration: a general root-owned import/signing boundary, atomic target reconciliation, complete pacman configuration handling, native ARM source/runtime validation, cross-ABI Omarchy VM upgrade/rollback coverage, and locked-desktop PAM acceptance. The successful manual deployment and unlocked camera verification do not replace those general integration checks.
