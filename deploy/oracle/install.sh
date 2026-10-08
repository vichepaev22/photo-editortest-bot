#!/usr/bin/env bash
# Fresh Ubuntu 24.04 installation only. Does not start or enable the bot.
set -euo pipefail
umask 077

fail() { printf '%s\n' "$*" >&2; exit 1; }
[[ $EUID -eq 0 ]] || fail 'Run this installer with sudo on the target VM.'
[[ $# -eq 1 && $1 =~ ^[0-9a-f]{40}$ ]] || fail 'Usage: install.sh <published 40-character commit SHA>'
release_commit=$1
source /etc/os-release
[[ $ID == ubuntu && $VERSION_ID == 24.04 ]] || fail 'Ubuntu 24.04 is required.'

app_dir=/opt/image-studio-bot/app
state_dir=/var/lib/image-studio-bot/live-pilot
unit_file=/etc/systemd/system/image-studio-bot.service
env_file=/etc/image-studio-bot.env
[[ ! -L /opt/image-studio-bot && ! -e $app_dir && ! -e $unit_file && ! -e $env_file ]] \
    || fail 'Existing installation detected. Preserve it and use the documented update procedure.'

apt-get update
DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
    ca-certificates git python3.12 python3.12-venv

install -d -m 0755 /opt/image-studio-bot
git init "$app_dir"
git -C "$app_dir" remote add origin https://github.com/vichepaev22/photo-editortest-bot.git
git -C "$app_dir" fetch --depth 1 origin "$release_commit"
git -C "$app_dir" checkout --detach "$release_commit"
[[ $(git -C "$app_dir" rev-parse HEAD) == "$release_commit" ]] || fail 'Commit mismatch.'

# Source remains root-owned and readable by the service account.
chmod -R a+rX "$app_dir"
python3.12 -m venv "$app_dir/.venv"
"$app_dir/.venv/bin/python" -m pip install -r "$app_dir/requirements.lock"
"$app_dir/.venv/bin/python" -m pip install --no-deps -e "$app_dir"
chmod -R a+rX "$app_dir/.venv" "$app_dir/src"

if ! getent passwd image-studio >/dev/null; then
    useradd --system --home-dir /var/lib/image-studio-bot --shell /usr/sbin/nologin image-studio
fi
install -d -o image-studio -g image-studio -m 0700 /var/lib/image-studio-bot "$state_dir"
install -d -m 0755 "$app_dir/data"
install -d -o image-studio -g image-studio -m 0700 "$app_dir/data/runtime"
install -o root -g root -m 0600 "$app_dir/deploy/oracle/server.env.example" "$env_file"
install -o root -g root -m 0644 "$app_dir/deploy/oracle/image-studio-bot.service" "$unit_file"
systemctl daemon-reload

printf '%s\n' \
    'Installation prepared. The bot has NOT been started or enabled.' \
    'Next: private credential transfer, region access check, consistent DB/media transfer, then single-poller cutover.'
