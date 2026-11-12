#!/usr/bin/env bash
# Interactive DigitalOcean console entrypoint. Never place a token in this file.
set +x
set -euo pipefail
cd /root/Dialian
export DDH_GUILD_ID=1570276882669240383
if [ -z "${TOKEN:-}" ]; then
    read -r -s -p 'Dialian 봇 토큰 (화면에 표시되지 않음): ' TOKEN </dev/tty
    printf '\n'
fi
export TOKEN
trap 'unset TOKEN' EXIT
bash deploy/install.sh
systemctl status dialian.service --no-pager
