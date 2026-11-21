#!/bin/bash
# Run inside the disposable official Omarchy VM, never on the workstation.
set -euo pipefail
[[ $(id -un) == "omarchy" && -d $HOME/compat-smoke/packages ]]
cd "$HOME/compat-smoke"
mkdir -p bin
# This is the public password of the official disposable test image.
printf 'omarchy\nomarchy\n' >vm-sudo-input
chmod 600 vm-sudo-input
cat >bin/sudo <<'SH'
#!/bin/bash
exec /usr/bin/sudo -S -p '' "$@" <"$HOME/compat-smoke/vm-sudo-input"
SH
chmod +x bin/sudo
export PATH="$PWD/bin:$PATH"
export OMARCHY_PATH=/usr/share/omarchy
export OMARCHY_UPDATE_UNATTENDED=1
unset OMARCHY_UPDATE_LOGGED
sudo env OMARCHY_UPDATE_PACMAN=1 pacman -Syu --noconfirm
sudo pacman -U --noconfirm packages/*.pkg.tar.zst
# The disposable image has no TPM and no enrolled biometric data. This test
# checks package/update operation; the workstation keeps TPM encryption on.
sudo sed -i 's/^encrypt_templates = true$/encrypt_templates = false/' /etc/gaze/config.toml
sudo systemctl enable --now gazed
omarchy-update -y
pacman -Q omarchy opencv gaze-bin gaze-gui-bin gaze-omarchy-bin
pacman -Dk
if ! systemctl is-active gazed; then
  journalctl -b -u gazed --no-pager -n 30
  exit 1
fi
gaze --version
for binary in /usr/bin/gazed /usr/bin/gaze /usr/bin/gaze-gui /usr/lib/security/pam_gaze.so; do
  ldd -r "$binary" >loader.log 2>&1
  if grep -E 'not found|undefined symbol' loader.log; then exit 1; fi
done
echo 'PASS: official Omarchy VM completed its stock update with the source-built Gaze group installed'
