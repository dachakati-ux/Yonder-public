# RHEL 8 system RPMs (offline)

Fetched from Rocky Linux 8 (+ EPEL for dkms) for air-gapped Yonder installs.

* `repo/` — local dnf repository (RPMs + repodata) copied into the USB pack
* Do not commit `*.rpm` (gitignored). Re-run `bash packaging/offline/fetch-system-rpms.sh` before `make offline-pack`.

`kernel-devel` in this tree must match the GPU host kernel (`uname -r`) for DKMS.
