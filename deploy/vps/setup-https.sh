#!/usr/bin/env bash
# Fresh Ubuntu 26.04 x86_64 only. Run after deploy/vps/install.sh:
#   sudo bash deploy/vps/setup-https.sh PUBLIC_IPV4
# Ports 80/443 must already be reachable. This script does not change a firewall,
# bot environment, data, SSH, or the bot service. It deliberately refuses reruns.
# Official sources checked 2026-10-08:
# https://letsencrypt.org/2026/03/11/shorter-certs-certbot
# https://eff-certbot.readthedocs.io/en/stable/using.html
# https://pypi.org/project/certbot/5.8.0/
# https://pypi.org/project/acme/5.8.0/
set -Eeuo pipefail
umask 077
export PATH=/usr/sbin:/usr/bin:/sbin:/bin

readonly APP_PYTHON=/opt/image-studio-bot/app/.venv/bin/python3.12
readonly CERTBOT_HOME=/opt/image-studio-bot/certbot
readonly CERTBOT_VERSION=5.8.0
readonly SITE=/etc/nginx/sites-available/image-studio-api
readonly ENABLED_SITE=/etc/nginx/sites-enabled/image-studio-api
readonly WEBROOT=/var/lib/letsencrypt
readonly RENEW_SERVICE=/etc/systemd/system/image-studio-certbot-renew.service
readonly RENEW_TIMER=/etc/systemd/system/image-studio-certbot-renew.timer
readonly DEPLOY_HOOK="${CERTBOT_HOME}/reload-nginx"
backup_dir=''
temporary_dir=''

die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }
cleanup() {
    local status=$?
    if [[ -n "$temporary_dir" ]]; then
        rm -f -- "$temporary_dir/http.conf" "$temporary_dir/https.conf"
        rmdir -- "$temporary_dir"
    fi
    if (( status != 0 )); then
        printf 'HTTPS setup stopped; inspect partial installation before retrying.\n' >&2
        [[ -z "$backup_dir" ]] || printf 'Nginx backup: %s\n' "$backup_dir" >&2
    fi
}
trap cleanup EXIT

[[ $EUID -eq 0 ]] || die 'Run as root with sudo.'
[[ $# -eq 1 ]] || die 'Usage: setup-https.sh PUBLIC_IPV4'
[[ -f /etc/os-release ]] || die 'Cannot identify OS.'
# shellcheck disable=SC1091
source /etc/os-release
[[ ${ID:-} == ubuntu && ${VERSION_ID:-} == 26.04 ]] || die 'Requires Ubuntu 26.04.'
[[ $(uname -m) == x86_64 ]] || die 'Requires x86_64.'
[[ -x "$APP_PYTHON" ]] || die 'Install the managed Python 3.12 app first.'
PUBLIC_IP=$("$APP_PYTHON" -I - "$1" <<'PY'
import ipaddress
import sys

if sys.version_info[:2] != (3, 12):
    raise SystemExit("Requires the managed Python 3.12 interpreter")
try:
    address = ipaddress.IPv4Address(sys.argv[1])
except ipaddress.AddressValueError:
    raise SystemExit("Provide a canonical public IPv4 address")
if (str(address) != sys.argv[1] or not address.is_global
        or address.is_multicast or address.is_reserved):
    raise SystemExit("Provide a canonical globally routable unicast IPv4 address")
print(address)
PY
)
readonly PUBLIC_IP
[[ -n "$PUBLIC_IP" ]] || die 'Invalid public IPv4 address.'

exec 9>/run/lock/image-studio-https-setup.lock
flock -n 9 || die 'Another HTTPS setup is running.'
for target in "$CERTBOT_HOME" "$SITE" "$ENABLED_SITE" "$RENEW_SERVICE" "$RENEW_TIMER" \
    /etc/letsencrypt /var/lib/letsencrypt /var/log/letsencrypt \
    /etc/systemd/system/nginx.service /etc/systemd/system/nginx.service.d; do
    [[ ! -e "$target" && ! -L "$target" ]] || die "Existing managed/conflicting path: $target"
done
command -v certbot >/dev/null 2>&1 && die 'An existing Certbot installation was found.'
if existing_certbot_units=$(systemctl list-unit-files '*certbot*' 'letsencrypt*' --no-legend --no-pager); then
    :
else
    unit_query_status=$?
    [[ $unit_query_status -eq 1 && -z "$existing_certbot_units" ]] \
        || die "Cannot query existing Certbot units (systemctl exit $unit_query_status)."
fi
[[ -z "$existing_certbot_units" ]] || die 'Existing Certbot/Let’s Encrypt systemd units were found.'

# Only an unmodified distribution Nginx configuration is accepted. Compare its
# conffile hashes with dpkg, and reject extra files/symlinks (including custom sites).
verify_stock_nginx() {
    local path checksum obsolete actual
    local -A stock_files=()
    [[ -d /etc/nginx && ! -L /etc/nginx ]] || die 'Invalid Nginx configuration directory.'
    [[ $(dpkg-query -W -f='${db:Status-Status}' nginx-common 2>/dev/null) == installed ]] \
        || die 'Existing Nginx configuration is not owned by the Ubuntu package.'
    while read -r path checksum obsolete; do
        [[ "$path" == /etc/nginx/* ]] || continue
        [[ -z "$obsolete" && -f "$path" && ! -L "$path" ]] \
            || die "Non-stock Nginx conffile: $path"
        actual=$(md5sum -- "$path")
        [[ ${actual%% *} == "$checksum" ]] || die "Modified Nginx conffile: $path"
        stock_files["$path"]=1
    done < <(dpkg-query -W -f='${Conffiles}\n' nginx-common)
    [[ ${stock_files[/etc/nginx/nginx.conf]:-} == 1 ]] || die 'No package Nginx configuration.'
    while IFS= read -r -d '' path; do
        [[ ${stock_files[$path]:-} == 1 ]] || die "Extra Nginx configuration file: $path"
    done < <(find /etc/nginx -type f -print0)
    while IFS= read -r -d '' path; do
        [[ "$path" == /etc/nginx/sites-enabled/default \
            && $(readlink -f -- "$path") == /etc/nginx/sites-available/default ]] \
            || die "Extra Nginx configuration symlink: $path"
    done < <(find /etc/nginx -type l -print0)
    while IFS= read -r -d '' path; do
        [[ "$path" == /etc/nginx/sites-enabled/default && -L "$path" ]] \
            || die "Existing enabled Nginx site: $path"
    done < <(find /etc/nginx/sites-enabled -mindepth 1 -maxdepth 1 -print0)
}

if [[ -e /etc/nginx || -L /etc/nginx ]]; then
    verify_stock_nginx
else
    command -v nginx >/dev/null 2>&1 && die 'A non-package Nginx installation was found.'
fi
[[ -z $(ss -H -ltn 'sport = :443') ]] || die 'Port 443 is already in use.'
while IFS= read -r listener; do
    [[ "$listener" == *'users:(("nginx"'* ]] || die 'Port 80 is used by a different service.'
done < <(ss -H -ltnp 'sport = :80')

apt-get update
DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends nginx ca-certificates openssl
verify_stock_nginx

"$APP_PYTHON" -I -m venv "$CERTBOT_HOME"
"$CERTBOT_HOME/bin/python" -I -m pip --isolated install --disable-pip-version-check \
    --index-url https://pypi.org/simple --only-binary=:all: \
    "certbot==$CERTBOT_VERSION" "acme==$CERTBOT_VERSION"
[[ $("$CERTBOT_HOME/bin/certbot" --version) == "certbot $CERTBOT_VERSION" ]] \
    || die 'Unexpected Certbot version.'
chown root:root "$CERTBOT_HOME"
chmod 0700 "$CERTBOT_HOME"

backup_dir=$(mktemp -d /var/backups/image-studio-https.XXXXXXXX)
cp -a -- /etc/nginx "$backup_dir/nginx"
temporary_dir=$(mktemp -d /run/image-studio-https.XXXXXXXX)
install -d -o root -g root -m 0755 "$WEBROOT" "$WEBROOT/.well-known" "$WEBROOT/.well-known/acme-challenge"
cat > "$temporary_dir/http.conf" <<NGINX
# Managed by image-studio-bot deploy/vps/setup-https.sh.
server {
    listen 80 default_server;
    server_name $PUBLIC_IP;
    server_tokens off;
    access_log off;
    client_max_body_size 1024;
    location ^~ /.well-known/acme-challenge/ {
        root $WEBROOT;
        default_type text/plain;
        try_files \$uri =404;
        limit_except GET HEAD { deny all; }
    }
    location / { return 404; }
}
NGINX
install -o root -g root -m 0644 "$temporary_dir/http.conf" "$SITE"
# Preserve the stock site file and its original symlink in the backup.
[[ ! -L /etc/nginx/sites-enabled/default ]] || unlink /etc/nginx/sites-enabled/default
ln -s -- "$SITE" "$ENABLED_SITE"
nginx -t
systemctl enable --now nginx
systemctl reload nginx

# IP certificates require the six-day shortlived profile and Certbot >=5.4.
# No --staging and no nginx plugin: obtain a publicly trusted production certificate.
"$CERTBOT_HOME/bin/certbot" certonly --non-interactive --agree-tos \
    --register-unsafely-without-email \
    --server https://acme-v02.api.letsencrypt.org/directory \
    --preferred-profile shortlived --preferred-challenges http \
    --webroot --webroot-path "$WEBROOT" --ip-address "$PUBLIC_IP" --cert-name "$PUBLIC_IP"
openssl x509 -in "/etc/letsencrypt/live/$PUBLIC_IP/cert.pem" -noout -checkip "$PUBLIC_IP"
openssl x509 -in "/etc/letsencrypt/live/$PUBLIC_IP/cert.pem" -noout -checkend 86400
openssl verify -CApath /etc/ssl/certs \
    -untrusted "/etc/letsencrypt/live/$PUBLIC_IP/chain.pem" "/etc/letsencrypt/live/$PUBLIC_IP/cert.pem"

cat "$temporary_dir/http.conf" > "$temporary_dir/https.conf"
cat >> "$temporary_dir/https.conf" <<NGINX
server {
    listen 443 ssl default_server;
    server_name $PUBLIC_IP;
    server_tokens off;
    access_log off;
    ssl_certificate /etc/letsencrypt/live/$PUBLIC_IP/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/$PUBLIC_IP/privkey.pem;
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_session_tickets off;
    add_header Strict-Transport-Security "max-age=31536000" always;
    add_header X-Content-Type-Options "nosniff" always;
    add_header Referrer-Policy "no-referrer" always;
    add_header Cache-Control "no-store" always;
    # Each photo is a separate octet-stream request. Merge uses two uploads:
    # exactly 10,000,000 bytes per photo, 20,000,000 bytes across both photos.
    client_max_body_size 10000000;
    location = /api/demo-session { return 404; }
    location = /api/demo-credits { return 404; }
    location /api/ {
        proxy_pass http://127.0.0.1:8089;
        proxy_http_version 1.1;
        # The application host guard accepts its loopback listener host.
        proxy_set_header Host 127.0.0.1:8089;
        proxy_set_header X-Forwarded-Host \$host;
        proxy_set_header X-Forwarded-For \$remote_addr;
        proxy_set_header X-Forwarded-Proto https;
        proxy_set_header X-Forwarded-Port 443;
        proxy_set_header Forwarded "";
        proxy_set_header Connection "";
        proxy_hide_header Cache-Control;
        proxy_connect_timeout 5s;
        proxy_read_timeout 240s;
        proxy_send_timeout 60s;
        # Keep private uploads/results out of Nginx temporary disk files.
        proxy_request_buffering off;
        proxy_buffering off;
    }
    location / { return 404; }
}
NGINX
install -o root -g root -m 0644 "$temporary_dir/https.conf" "$SITE"
nginx -t
systemctl reload nginx

cat > "$DEPLOY_HOOK" <<'HOOK'
#!/usr/bin/env bash
set -euo pipefail
export PATH=/usr/sbin:/usr/bin:/sbin:/bin
/usr/sbin/nginx -t && /usr/bin/systemctl reload nginx
HOOK
chown root:root "$DEPLOY_HOOK"
chmod 0700 "$DEPLOY_HOOK"
cat > "$RENEW_SERVICE" <<SYSTEMD
[Unit]
Description=Renew Image Studio six-day IP certificate
Wants=network-online.target
After=network-online.target nginx.service

[Service]
Type=oneshot
User=root
Group=root
UMask=0077
ExecStart=$CERTBOT_HOME/bin/certbot renew --cert-name $PUBLIC_IP --non-interactive --quiet --no-directory-hooks --deploy-hook $DEPLOY_HOOK
TimeoutStartSec=300
PrivateTmp=true
NoNewPrivileges=true
ProtectHome=true
ProtectSystem=full
ReadWritePaths=/etc/letsencrypt /var/lib/letsencrypt /var/log/letsencrypt
SYSTEMD
cat > "$RENEW_TIMER" <<'SYSTEMD'
[Unit]
Description=Check Image Studio IP certificate renewal twice daily

[Timer]
OnCalendar=*-*-* 00,12:00:00
RandomizedDelaySec=30min
AccuracySec=1min
Persistent=true

[Install]
WantedBy=timers.target
SYSTEMD
chown root:root "$RENEW_SERVICE" "$RENEW_TIMER"
chmod 0644 "$RENEW_SERVICE" "$RENEW_TIMER"
systemctl daemon-reload
systemctl enable --now image-studio-certbot-renew.timer
printf 'HTTPS API: https://%s/api/\nNginx backup: %s\n' "$PUBLIC_IP" "$backup_dir"
printf 'Renewal timer enabled; bot environment and bot service were not changed.\n'
