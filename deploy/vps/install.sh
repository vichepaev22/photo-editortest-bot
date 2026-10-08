#!/usr/bin/env bash
# Prepare a fresh Ubuntu VPS. Starting the bot requires a separate state cutover.
set -euo pipefail
umask 077

fail() { printf '%s\n' "$*" >&2; exit 1; }
[[ $EUID -eq 0 ]] || fail 'Run as root on the target VPS.'
[[ $# -eq 1 && $1 =~ ^[0-9a-f]{40}$ ]] || fail 'Usage: install.sh <published 40-character commit SHA>'
release_commit=$1
source /etc/os-release
[[ $ID == ubuntu && ( $VERSION_ID == 24.04 || $VERSION_ID == 26.04 ) ]] \
    || fail 'Supported target: Ubuntu 24.04 or 26.04.'

base_dir=/opt/image-studio-bot
app_dir=$base_dir/app
state_dir=/var/lib/image-studio-bot/live-pilot
unit_file=/etc/systemd/system/image-studio-bot.service
env_file=/etc/image-studio-bot.env
[[ ! -e $base_dir && ! -L $base_dir && ! -e /var/lib/image-studio-bot \
    && ! -L /var/lib/image-studio-bot && ! -e $unit_file && ! -L $unit_file \
    && ! -e $env_file && ! -L $env_file ]] \
    || fail 'Existing installation detected; preserve it and use an update procedure.'

apt-get update
DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
    ca-certificates git python3-venv

# Keep the tested Python minor version without changing system Python or shell PATH.
install -d -m 0755 "$base_dir"
python3 -m venv "$base_dir/tools"
"$base_dir/tools/bin/python" -m pip --isolated install \
    --index-url https://pypi.org/simple --only-binary=:all: uv==0.12.23
export UV_PYTHON_INSTALL_DIR=$base_dir/python
export UV_CACHE_DIR=$base_dir/cache
"$base_dir/tools/bin/uv" python install 3.12 --no-bin
"$base_dir/tools/bin/uv" venv --managed-python --python 3.12 --seed "$app_dir/.venv"

git init "$app_dir"
git -C "$app_dir" remote add origin https://github.com/vichepaev22/photo-editortest-bot.git
git -C "$app_dir" fetch --depth 1 origin "$release_commit"
git -C "$app_dir" checkout --detach "$release_commit"
[[ $(git -C "$app_dir" rev-parse HEAD) == "$release_commit" ]] || fail 'Commit mismatch.'
"$app_dir/.venv/bin/python" -m pip --isolated install \
    --index-url https://pypi.org/simple -r "$app_dir/requirements.lock"
"$app_dir/.venv/bin/python" -m pip --isolated install \
    --index-url https://pypi.org/simple --no-deps -e "$app_dir"
chmod -R a+rX "$base_dir/python" "$app_dir"

if ! getent passwd image-studio >/dev/null; then
    useradd --system --home-dir /var/lib/image-studio-bot --shell /usr/sbin/nologin image-studio
fi
install -d -o image-studio -g image-studio -m 0700 /var/lib/image-studio-bot "$state_dir"
install -d -m 0755 "$app_dir/data"
install -d -o image-studio -g image-studio -m 0700 "$app_dir/data/runtime"
install -o root -g root -m 0600 "$app_dir/deploy/oracle/server.env.example" "$env_file"
install -o root -g root -m 0644 "$app_dir/deploy/oracle/image-studio-bot.service" "$unit_file"
systemctl daemon-reload

printf '%s\n' 'Prepared; service remains stopped and disabled.' \
    'Transfer private settings and a consistent snapshot before starting one poller.'
