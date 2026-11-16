# Running Keel on a private server

Keel is built for **one person**. This guide puts it on a small Linux server (a VPS or a machine at home) so it is
always on for check-ins and reachable from your phone, without opening it to anyone else. For a first run on your own
computer, start with **[INSTALL.md](INSTALL.md)**.

The compose commands in this guide (start, authenticate, back up, copy out, restore) are run by the `docker` CI job on
every push. The Caddy, firewall and cron steps are standard but are **not** exercised by CI: check them on your server.

## What you are responsible for

Keel protects against a compromised *model*. On a server, the rest is yours:

| Requirement | Why | Where |
|---|---|---|
| A strong `API_KEY` | The only thing between the internet and your data. There are no user accounts. | Step 2 |
| TLS in front | Keel speaks plain HTTP; the key would travel in clear text. | Step 4 |
| A firewall | Nothing but SSH (and 443 if you choose public access) should be reachable. | Step 3 |
| Protected database and backups | They hold everything you told Keel, unencrypted. | Steps 5 and 6 |
| One process | The scheduler and the rate limiter live in memory. Two copies means duplicate check-ins. | Step 2 |
| Logs with a limit | So they do not fill the disk or keep more than you want. | Step 7 |

If you cannot do all of these, use **private access** in step 4 and never expose a port.

## 1. Prepare the server

A 1 GB server running Ubuntu 24.04 or Debian 12 is enough. As a non-root user with sudo:

```bash
sudo apt update && sudo apt install -y git ufw
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker "$USER"     # log out and in again
sudo mkdir -p /opt/keel && sudo chown "$USER" /opt/keel
git clone https://github.com/malaikamalik23/Keel.git /opt/keel && cd /opt/keel
```

Turn on full-disk encryption if your provider offers it (see step 6).

## 2. Configure and start

```bash
cp .env.example .env && chmod 600 .env
python3 -c "import secrets; print('API_KEY=' + secrets.token_urlsafe(32))" >> .env
nano .env        # set an LLM key, AGENT_MODEL, AGENT_TIMEZONE; Telegram if you want messages on your phone
docker compose up -d --build
curl -fsS http://127.0.0.1:8000/health          # {"status":"ok"}
```

What the compose file already does for you:

- publishes Keel on `127.0.0.1:8000` **only**. Do not change this to `0.0.0.0`. Docker publishes ports past `ufw`, so
  that one edit would put plain HTTP on the internet.
- refuses to start without `API_KEY`.
- runs as a non-root user with a read-only filesystem and no capabilities; only the `keel-data` volume is writable.
- runs **one** container with the scheduler on. Do not scale it, and do not also run `python -m app.scheduler`.
- keeps at most 50 MB of container logs.

Check the key works, and that nothing answers without it:

```bash
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8000/api/tasks                  # 401
curl -s -o /dev/null -w '%{http_code}\n' -H "X-API-Key: <your key>" http://127.0.0.1:8000/api/tasks   # 200
```

## 3. Firewall

```bash
sudo ufw default deny incoming
sudo ufw default allow outgoing
sudo ufw allow OpenSSH
sudo ufw enable
```

Use SSH keys and turn off password logins (`PasswordAuthentication no` in `/etc/ssh/sshd_config`).

Outgoing: Keel's web fetcher refuses private and internal addresses on its own, but on a server that can reach other
internal machines, also block those ranges for the container at the network level.

## 4. Reach it

### Option A: private access (recommended)

Nothing is exposed. From your laptop:

```bash
ssh -N -L 8000:127.0.0.1:8000 you@your-server
```

Then open http://127.0.0.1:8000 on the laptop and enter your `API_KEY`. SSH provides the encryption.
A private network such as WireGuard or Tailscale does the same job for a phone. Telegram needs no inbound access at
all: Keel calls out to Telegram, so check-ins and chat from your phone work with every port closed.

### Option B: public HTTPS with Caddy

Only if you need the dashboard from anywhere. You need a domain name pointing at the server.

```bash
sudo apt install -y caddy
sudo ufw allow 80,443/tcp
```

`/etc/caddy/Caddyfile`:

```
keel.example.com {
    reverse_proxy 127.0.0.1:8000
}
```

```bash
sudo systemctl reload caddy
```

Caddy gets and renews the certificate by itself. Open `https://keel.example.com` and enter your `API_KEY`.

About proxy headers: Caddy adds `X-Forwarded-For`. Keel does not use it to identify you (the key does that), so there
is nothing to configure and nothing a client can spoof. Two consequences worth knowing:

- Keel's rate limit (`RATE_LIMIT_PER_MINUTE`) sees every request as coming from the proxy, so it is one shared limit.
  That is fine for one user. It is not protection against someone guessing keys, which is why the key must be long
  and random. For more, add rate limiting or an IP allow-list in Caddy.
- If `API_KEY` were ever removed, Keel would refuse every proxied request instead of serving them unauthenticated.

## 5. Backups

A backup is a consistent copy taken while Keel runs. Take one now:

```bash
docker compose exec -T keel python -m scripts.backup          # -> /app/data/backups inside the volume, newest 14 kept
mkdir -p ~/keel-backups && chmod 700 ~/keel-backups
docker compose cp keel:/app/data/backups/. ~/keel-backups/
```

Make it nightly with `crontab -e`:

```
15 3 * * * cd /opt/keel && docker compose exec -T keel python -m scripts.backup && docker compose cp keel:/app/data/backups/. $HOME/keel-backups/
```

A backup on the same disk is not a backup. Send `~/keel-backups` somewhere else, encrypted, with a tool you trust
(`restic` and `rclone crypt` both encrypt before uploading). Backups contain everything Keel knows about you; `.env`
is deliberately not in them, so keep a copy of your keys in a password manager.

**Restore** (also how you move to a new server):

```bash
cd /opt/keel
docker compose stop keel
docker compose run --rm --no-deps -T keel sh -c 'cat > /app/data/agent.db' < ~/keel-backups/agent-<timestamp>.db
docker compose up -d
```

Practise a restore once, before you need it.

## 6. Data at rest

The database is one SQLite file in the `keel-data` volume and is **not encrypted by Keel** (the reasons are in
[SECURITY.md](../SECURITY.md#your-data-retention-backups-encryption)). On a server that means:

- use the provider's disk encryption or LUKS, so a discarded or snapshotted disk is unreadable;
- remember that provider snapshots and your backup copies are copies of your data too;
- keep `.env` at mode 600. It holds your LLM key, Telegram token and `API_KEY`.
- backups are written readable by their owner only (mode 600); keep the folder you copy them to at mode 700, as above.

Keel keeps your history until you delete it. To erase everything: `docker compose down -v`, then delete your backups.

## 7. Logs and monitoring

- **Application log:** `docker compose logs --tail 100 keel`. It records requests and errors, never keys or tokens.
  Docker keeps 5 files of 10 MB and discards older output; change `logging` in `docker-compose.yml` to keep more or less.
- **Audit log:** every action the agent took, queued or was refused is in the database (dashboard → Activity, or
  `GET /api/audit-log`). It is part of your backups and is kept until you delete the database.
- **Is it up?** Point any uptime checker at `/health` (it needs no key and reveals nothing). `docker compose ps` shows
  the container's own health check. `restart: unless-stopped` brings Keel back after a crash or reboot.
- **Is it being probed?** With option B, watch Caddy's access log for bursts of `401` responses.

## 8. Updating

```bash
cd /opt/keel
docker compose exec -T keel python -m scripts.backup
git pull
docker compose up -d --build
```

Database migrations run automatically at startup. If an update goes wrong, check out the previous commit, restore
the backup you just took (step 5) and start again.

## Checklist

- [ ] `API_KEY` is long and random, and `/api/tasks` answers 401 without it
- [ ] `.env` is mode 600 and not in any repository
- [ ] Port 8000 is not reachable from outside (`curl http://<server-ip>:8000/health` from another machine fails)
- [ ] Access is by SSH tunnel or VPN, or by HTTPS only
- [ ] A nightly backup runs, leaves the server encrypted, and you have restored one
- [ ] The disk is encrypted
- [ ] Exactly one Keel container is running
