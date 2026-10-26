# Ubuntu installer preparation

Checked 2026-10-23 UTC against Canonical's [release directory](https://releases.ubuntu.com/26.04/) and [published checksums](https://releases.ubuntu.com/26.04/SHA256SUMS).

Selected candidate: Ubuntu 26.04.1 LTS desktop AMD64, `ubuntu-26.04.1-desktop-amd64.iso` (approximately 6 GB). Published SHA-256: `601e30fbf5d97759367c632e2c33630665039b7e2158fd068403da3ccf1bda1f`.

The detached checksum signature passed verification on the Surface using an isolated GnuPG home and the fingerprint `843938DF228D22F7B3742BC0D94AA3F0EFE21092` from [Canonical's verification procedure](https://ubuntu.com/tutorials/how-to-verify-ubuntu). The checksum file, detached signature, public signing key, verification log and result were copied back with matching recorded hashes to `ubuntu-signature-verified/`. No global keyring changed. The ISO itself has not been downloaded or authenticated; its actual digest must match the signed checksum before media writing.

Do not consume the internal backup capacity with an additional ISO copy before recalculating the reserve. The full backup is still pending. The recovery flash cannot be overwritten until its required contents have been independently backed up and verified. Ubuntu remains targeted at the 2 TB SSD, preserving its existing data; no partition, firmware or boot changes have been performed.
