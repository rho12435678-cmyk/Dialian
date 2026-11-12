#!/usr/bin/env bash
# Existing root-owned VPS, invoked with TOKEN and DDH_GUILD_ID in the environment.
set -euo pipefail
cd /root/Dialian
umask 077
: "${TOKEN:?TOKEN must be set}"
: "${DDH_GUILD_ID:?DDH_GUILD_ID must be set}"
command -v systemctl >/dev/null
python3 -c 'import sys; assert sys.version_info >= (3, 12), "Python 3.12+ is required"'
python3 -m venv venv-ddh
venv-ddh/bin/python -m pip install -r requirements.txt
venv-ddh/bin/python dial.py --check-config
venv-ddh/bin/python -m unittest discover -s tests -q
# Validate Discord login and target access BEFORE stopping the old bot.
venv-ddh/bin/python - <<'PY'
import asyncio
import discord
from config import Settings
from ddh.server import REQUIRED_PERMISSIONS
async def check():
    config = Settings.from_env()
    async with discord.Client(intents=discord.Intents.none()) as client:
        await client.login(config.token)
        guild = await client.fetch_guild(config.guild_id)
        me = await guild.fetch_member(client.user.id)
        missing = [p for p in REQUIRED_PERMISSIONS if not getattr(me.guild_permissions, p)]
        if missing:
            raise RuntimeError('Missing bot permissions: ' + ', '.join(missing))
asyncio.run(check())
PY
mkdir -p deploy-backups
if [ -f .env.ddh ]; then
    cp .env.ddh "deploy-backups/ddh-env-$(date +%Y%m%d-%H%M%S)"
fi
venv-ddh/bin/python - <<'PY'
import os
from pathlib import Path
from config import Settings
s = Settings.from_env()
p = Path('.env.ddh.next')
p.write_text('TOKEN=' + s.token + '\nDDH_GUILD_ID=' + str(s.guild_id) +
             '\nDDH_DATABASE=data/ddh.db\n', encoding='utf-8')
p.chmod(0o600)
p.replace('.env.ddh')
PY
# Stop the previous nohup launch only after installation and preflight succeeded.
# Match the original repository's exact entrypoint, never an arbitrary saved PID.
pkill -f '(^|/)(python|python3)( -u)? dial[.]py$' || true
install -m 0644 deploy/dialian.service /etc/systemd/system/dialian.service
systemctl daemon-reload
systemctl enable dialian.service
DDH_STARTED_AT="$(date --iso-8601=seconds)"
systemctl restart dialian.service
sleep 5
systemctl is-active --quiet dialian.service
# Poll the service's readiness marker; merely having a live process is insufficient.
for attempt in $(seq 1 12); do
    if journalctl -u dialian.service --since "$DDH_STARTED_AT" --no-pager | grep -q "Dialian connected to DDH guild $DDH_GUILD_ID;"; then
        exit 0
    fi
    sleep 3
done
printf '%s\n' 'DDH gateway readiness was not confirmed. Inspect journalctl -u dialian.service.' >&2
exit 1
