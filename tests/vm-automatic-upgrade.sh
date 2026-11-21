#!/bin/bash
# Full automatic path in the disposable official VM. Run as its omarchy user.
set -euo pipefail
[[ $(id -un) == "omarchy" && -d $HOME/automatic-proof/old-packages ]]
cd "$HOME/automatic-proof"
mkdir -p bin results
diagnostics() {
  local status=$?
  if (( status != 0 )); then
    find "$HOME/.local/state/omarchy/update-compat" -name source-build.log -exec cat {} \; 2>/dev/null || true
  fi
  exit "$status"
}
trap diagnostics EXIT
# Published disposable-image password; no workstation credentials enter this VM.
printf 'omarchy\nomarchy\n' >vm-sudo-input
chmod 600 vm-sudo-input
cat >bin/sudo <<'SH'
#!/bin/bash
exec /usr/bin/sudo -S -p '' "$@" <"$HOME/automatic-proof/vm-sudo-input"
SH
chmod +x bin/sudo
export PATH="$PWD/bin:$PATH" OMARCHY_PATH=/usr/share/omarchy
export XDG_RUNTIME_DIR=/run/user/$(id -u)
export DBUS_SESSION_BUS_ADDRESS="unix:path=$XDG_RUNTIME_DIR/bus"
sudo env OMARCHY_UPDATE_PACMAN=1 pacman -Syu --noconfirm --needed podman base-devel
sudo pacman -U --noconfirm component/*.pkg.tar.zst old-packages/*.pkg.tar.zst
# Empty test image: it has no TPM or personal enrollment. Host policy is untouched.
sudo sed -i 's/^encrypt_templates = true$/encrypt_templates = false/' /etc/gaze/config.toml
if ! grep -q '^omarchy:' /etc/subuid; then
  sudo usermod --add-subuids 100000-165535 --add-subgids 100000-165535 omarchy
fi
podman info --format '{{.Host.Security.Rootless}}'
[[ $(podman info --format '{{.Host.Security.Rootless}}') == "true" ]]
omarchy-update-compat track gaze
mkdir -p "$HOME/.cache/omarchy/update-compat"
cp -r publisher-cache/. "$HOME/.cache/omarchy/update-compat/"

status=0
omarchy-update-compat --offline plan --refresh --json >results/before.json || status=$?
[[ $status == "10" ]]
python - <<'PY'
import json
p=json.load(open('results/before.json'))
assert p['native_baseline']['compatible']
assert p['status']=='build-required'
assert p['applications'][0]['upstream_preparation']['compatible'] is False
assert any(x['dependency']=='opencv>=5.1' for x in p['applications'][0]['upstream_preparation']['missing'])
print('PASS: reproduced published Gaze/OpenCV mismatch from real Gaze 0.3.7')
PY

# One command now builds, stages and deploys, then finishes the stock updater.
omarchy-update-compat --offline upgrade -y | tee results/first-upgrade.log
omarchy-update-compat status >results/after.json
python - <<'PY'
import json,subprocess
p=json.load(open('results/after.json'))
assert p['status']=='ready' and p['applications'][0]['action']=='build-source'
assert p['applications'][0]['source_provenance']['cache_hit'] is False
assert subprocess.check_output(['pacman','-Q','gaze-bin'],text=True).strip()=='gaze-bin 0.5.2-1.1'
assert 'INSTALLING' in open('results/first-upgrade.log').read()
assert 'DONE' in open('results/first-upgrade.log').read()
print('PASS: uncached source build and native deployment completed automatically')
print(json.dumps(p['applications'][0]['source_provenance'],sort_keys=True))
PY
sudo systemctl enable --now gazed
systemctl is-active gazed
pacman -Dk
gaze --version
for binary in /usr/bin/gazed /usr/bin/gaze /usr/bin/gaze-gui /usr/lib/security/pam_gaze.so; do
  ldd -r "$binary" >results/loader.log 2>&1
  if grep -E 'not found|undefined symbol' results/loader.log; then exit 1; fi
done

omarchy-update-compat --offline upgrade -y | tee results/second-upgrade.log
omarchy-update-compat status >results/repeated.json
python - <<'PY'
import json
p=json.load(open('results/repeated.json'))
assert p['applications'][0]['already_installed']
assert 'bundle' not in p
text=open('results/second-upgrade.log').read()
assert 'KEEP' in text and 'BUILDING' not in text and 'INSTALLING' not in text
print('PASS: repeat request keeps the compatible installed build without rebuilding or reinstalling')
PY
echo 'PASS: original mismatch -> automatic build -> guarded deploy -> stock updater -> repeat KEEP'
