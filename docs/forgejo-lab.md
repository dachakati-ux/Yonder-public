# Lab Forgejo (ai1) — loopback + SSH tunnel

**LAB ONLY — PENDING SECURITY REVIEW / REVISION**

Forgejo on ai1 is a local git forge for company-specific tooling repos.
It is **not** wired into the Yonder coder (`ForgejoAdapter` stays
fail-closed). Reach it the same way as the console: SSH local forward.

## Layout

| Item | Path / value |
| --- | --- |
| Listen | `127.0.0.1:3001` only |
| Binary | `/opt/yonder/forgejo/forgejo` |
| Data (repos, sqlite, logs) | `/srv/yonder-storage/forgejo` |
| Config | `/etc/yonder/forgejo.ini` |
| Secrets | `/etc/yonder/forgejo.env` (mode 0600) |
| Unit | `yonder-forgejo.service` |

Templates in-repo:

* [`config/lab/forgejo.ini.example`](../config/lab/forgejo.ini.example)
* [`packaging/offline/systemd/yonder-forgejo.service`](../packaging/offline/systemd/yonder-forgejo.service)
* [`scripts/provision-ai1-forgejo.sh`](../scripts/provision-ai1-forgejo.sh)

## Provision (ai1)

Air-gapped preferred (Mac has network; ai1 often does not):

```bash
# On a networked machine:
curl -fsSL -o /tmp/forgejo-16.0.2-linux-amd64 \
  https://code.forgejo.org/forgejo/forgejo/releases/download/v16.0.2/forgejo-16.0.2-linux-amd64
scp /tmp/forgejo-16.0.2-linux-amd64 ai1:/opt/yonder/
# Sync this checkout to ai1:/opt/yonder/src if needed.

# On ai1:
sudo FORGEJO_BINARY=/opt/yonder/forgejo-16.0.2-linux-amd64 \
  FORGEJO_ADMIN_USER=labadmin \
  FORGEJO_ADMIN_PASSWORD='choose-a-strong-lab-password' \
  FORGEJO_ADMIN_EMAIL=labadmin@localhost.local \
  /opt/yonder/src/scripts/provision-ai1-forgejo.sh
```

The script refuses any `HTTP_ADDR` other than loopback, installs the binary,
writes config + secret, migrates sqlite, enables systemd, and verifies
`127.0.0.1:3001` only.

## Reach from a laptop

```bash
ssh -N -L 3001:127.0.0.1:3001 ai1
# browse http://127.0.0.1:3001
```

Combine with console/gateway forwards if needed:

```bash
ssh -N \
  -L 3000:127.0.0.1:3000 \
  -L 3001:127.0.0.1:3001 \
  -L 8080:127.0.0.1:8080 \
  ai1
```

## First-admin hardening checklist

1. Sign in as the admin created by the provision script (or complete the
   one-time install UI if you skipped admin env vars).
2. Confirm **Disable registration** is on (`DISABLE_REGISTRATION = true` in
   `forgejo.ini`).
3. Create an organization (recommended name: `yonder-lab`) for tooling repos.
4. Create personal access tokens only when needed for later Phase L coder
   wiring. Store them as managed secrets — **never** commit tokens. Yonder
   config fields `coder.forgejo_base_url` / `coder.forgejo_token_ref` stay
   empty until that slice.

## Git over HTTP (through the tunnel)

```bash
git clone http://127.0.0.1:3001/yonder-lab/example.git
```

Built-in Forgejo SSH is **off** in v1 (`DISABLE_SSH` / `START_SSH_SERVER=false`).

## Backup note

Repos and sqlite live under `/srv/yonder-storage/forgejo`. Include that tree
in whatever backup story covers `/srv/yonder-storage`. No separate Forgejo
backup job is shipped in this slice.

## What this is not

* Not a corporate IAM / upstream IAM login
* Not bound on the lab NIC (`0.0.0.0`)
* Not Yonder coder clone/push/PR automation (see [`docs/coder.md`](./coder.md)
  and Phase L in [`docs/coder-autonomy-plan.md`](./coder-autonomy-plan.md))
