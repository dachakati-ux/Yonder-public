# EL8 exFAT FUSE RPMs (offline)

Fetched from Rocky Linux 8 + EPEL + RPM Fusion free for air-gapped mounts of
legacy exFAT media on RHEL 8.10.

* `repo/` — local dnf repository (RPMs + repodata)
* Install on the host with `bash packaging/offline/enable-exfat.sh --rpm-dir <path>`
* One-time use: mount exFAT → copy onto XFS/ext4 → unmount. Do not keep
  production model roots on exFAT.

Do not commit `*.rpm` (gitignored). Re-run `bash packaging/offline/fetch-exfat-rpms.sh`.
