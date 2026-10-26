# Confirmed portable Ubuntu target

The user confirmed on October 22, 2026 that Ubuntu and Prometheus should be installed on the existing **2 TB Prometheus SSD**, so the system can boot on another compatible computer. The separate 32 GB recovery flash is not the installation target. Finish the current companion recovery work first; audit the saved plans and candidate releases against implemented, tested and installed evidence. Routine display updates remain off.

## Intended acceptance

- A persistent Ubuntu installation on the external SSD, with its boot files on that SSD and Prometheus installed for Linux.
- Preserve the current Windows Prometheus data, authorization history, rollback and repository evidence through a verified off-target backup and a reviewed partition plan.
- Demonstrate external boot without relying on the Dell internal boot drive, then test on another compatible x86-64 UEFI machine. Universal compatibility with arbitrary computers is not claimed.
- Verify Linux owner authentication, project approval/history/undo, runtime/resource reporting, model/reviewer behavior, restart and removable-drive shutdown. Unsupported Windows-only features must remain visible until replaced and qualified.

## Read-only capacity observation

At inspection: C: OS has 253,311,143,936 bytes free; E: Prometheus-2TB has volume size 1,950,372,327,424 bytes and 1,751,220,875,264 bytes free; D: PROMETHEUS has size 32,076,988,416 bytes and 811,859,968 bytes free. All report Healthy. These are observations, not a backup or filesystem consistency proof. Re-identify drives before every write.

Approximately 199.2 GB of the SSD is used. C: may accommodate an off-SSD file backup while preserving useful free space, but actual selected-file inventory, locked files, snapshot consistency, copy verification and space reserve must be checked first. Backing up both full drives plus installer media would leave substantially less headroom. The user has been asked whether a separate empty 8 GB or larger installer USB is available. Existing recovery-flash contents are not disposable.

## Unfinished gates

Current companion R5 remains installed and R7 remains a candidate. Production migration and pointer activation, installed acceptance and physical lifecycle checks are unfinished. The broad plan/candidate audit is ongoing; earlier planning documents contain both superseded completed work and unimplemented future designs. Linux compatibility rehearsal, exact partition/EFI layout, verified off-drive backup, installer preparation and real boot tests are still pending. No formatting, shrinking, partition creation or boot changes have been performed.

Canonical documentation consulted: [Install Ubuntu Desktop](https://ubuntu.com/desktop/docs/en/latest/tutorial/install-ubuntu-desktop/) and [Create a bootable USB stick](https://ubuntu.com/desktop/docs/en/latest/how-to/create-a-bootable-usb-stick/). Installer-media creation overwrites that media; simply copying an ISO does not install Ubuntu or make Prometheus portable.
