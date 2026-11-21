# Acceptance before automatic deployment

This checklist supplements the existing native dependency, package, runtime, and Omarchy regression tests. A passing package build alone is not release acceptance. Record each result as passed, failed, or not exercised, with the exact component/source revision, architecture, starting packages, target repositories, and sanitized evidence.

| Area | Test | Required outcome |
| --- | --- | --- |
| Trust | Alter an archive, source pin, recipe, cache entry, or approved policy | Reject it before the package transaction; identify the failed check. Never approve a new source or signing key from an error message. |
| Trust | Try an untracked package, arbitrary root command/configuration, symlink, or writable input | Reject it. Builds remain unprivileged; the installed privileged helper accepts only the tracked package group and existing full-upgrade flags. |
| Trust | Review dependencies and advisories for the proposed release | Record the review date, tools, findings, and exceptions. A checksum proves identity, not absence of vulnerabilities. Keep repository signature policy intact. |
| Stability | Interrupt a build; exhaust time, memory, PIDs, output, or disk | Stop the owned build, leave the host package database unchanged, explain the failure, and permit a clean retry. Do not interrupt native pacman while it commits. |
| Stability | Run two coordinators or change installed packages/repositories/policy during preparation | Refuse concurrent or stale work; recheck the selected target under pacman's transaction lock. |
| Stability | Exercise compiler failure, unavailable mirror, corrupt cache, and failed native preparation | No READY/DONE message, no deployment of a partial package group, and no automatic retry loop. |
| Stability | Reboot after deployment and exercise Gaze's daemon, GUI, PAM, camera and desktop integration | Healthy service and loader checks; successful face authentication and password fallback. A VM without a camera/TPM cannot prove workstation authentication. |
| Recovery | Rehearse snapshot/package recovery in a disposable VM | A documented recovery restores the starting bootable system and authentication path. Preserve user settings and enrollment; never publish biometric material. |
| Performance | Measure cold build and warm/no-change upgrade | Report elapsed time and resource limits. The warm path performs no source build or reinstall; no background daemon or idle polling is introduced. |
| Simplicity | Use one tracking setting and one ordinary upgrade request | Explain CHECKING/PLAN/BUILDING/READY/INSTALLING/DONE/KEEP/BLOCKED in plain language. Existing upgrade confirmation and authentication remain the only routine prompts. |
| Simplicity | Cancel, retry, disable tracking, or receive a blocked result | State what changed, what remains installed, and the next useful command; no stack trace or false success. Retain a bounded diagnostic log. |
| Lifecycle | Make a compatible publisher package available, including a lower packaging revision | Replace the local build in the same native system transaction, then keep that published version without rebuilding. Preserve all three package identities and install scripts. |
| Lifecycle | Upgrade the coordinator itself and change pacman/libalpm's ABI | Supply a matching native helper in the distro's package transaction; the running planner and subsequent installed verifier both work. A standalone locally installed helper needs an explicit maintenance path until it is packaged in a maintained repository. |
| Lifecycle | Introduce an official native repository package for a managed application | Prefer the native provider and retire the fallback cleanly; a local or publisher wrapper must not shadow the distro upgrade. The present publisher-only adapter does not establish this general provider behavior. |
| Portability | Reproduce from a fresh official image for each supported architecture | Test actual native build and runtime. Explicitly reject unsupported architectures; x86_64 evidence does not establish aarch64 support. |
| Contribution | Run focused regressions and the full required Omarchy VM suite | Existing updater ordering, keyring handling, snapshot, migrations, systemd scope and AUR processing continue to work. Review the diff and sanitize every published artifact. |

The initial release gate is the x86_64 path. Live face unlock and password unlock after face cancellation have passed on the current x86_64 deployment; see [the physical record](validation/physical-lock.json). Native aarch64 source/runtime, reboot/recovery and daemon-outage fallback remain unproved. Two independent builds and artifact comparison are useful follow-up evidence; do not claim reproducible builds until that comparison passes.

## Boundaries and references

Source integrity and build integrity are separate checks ([SLSA threat model](https://slsa.dev/spec/v1.2/threats-overview)). A local provenance JSON is a diagnostic record, not a signed attestation or a SLSA level. The desktop administrator and rootless container engine remain trusted; this component grants no passwordless privilege escalation.

Use native repository signature/ignore settings ([pacman.conf](https://man.archlinux.org/man/pacman.conf.5.en)) and explicit container isolation/resource controls ([Podman run](https://docs.podman.io/en/latest/markdown/podman-run.1.html)). Pin the reviewed application revision and external build artifacts. Keep dependency fetching separate from network-disabled compilation, with no workstation credentials, home directory, devices, or container-engine socket mounted into the builder.
