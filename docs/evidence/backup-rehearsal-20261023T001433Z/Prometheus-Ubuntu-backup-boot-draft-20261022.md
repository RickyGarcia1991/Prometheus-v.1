# Portable Ubuntu backup and boot preparation — October 22, 2026

Preparation only. Finish the companion milestone and preceding capability qualification before applying platform changes. Installed core dev55 and reconnect R5 remain the baseline. No partition, firmware, encryption, boot entry or filesystem has been changed.

## Observed target and proposed layout

The user selected the 2 TB Prometheus SSD, not the 32 GB recovery flash. The observed SSD is GPT, 1,950,389,231,104 bytes, with a small Microsoft reserved partition and a 1,950,372,331,520-byte NTFS data partition. It currently has no EFI System Partition or Linux root partition. Roughly 1.75 TB free inside NTFS is not unallocated partition space. Re-resolve the physical identity, volume identifiers and geometry at every write boundary; drive letters are temporary observations.

Proposed allocation for qualification: retain the existing Windows-readable data partition and its contents; reserve approximately 514 GiB at the end for a 1 GiB FAT32 EFI System Partition, a 1 GiB Linux boot partition and approximately 512 GiB Linux system/data allocation. This is a sizing proposal, not a shrink instruction: filesystem shrink limits, alignment and a tested encrypted installation procedure must determine exact offsets. Preserve the existing Microsoft reserved partition. Do not use the installer's whole-disk erase option on the populated SSD.

Prefer a portable passphrase-encrypted Linux allocation after its recovery procedure is tested; avoid making portable boot depend on the Dell's TPM. The owner must enter and retain the encryption secret. Keep the existing NTFS data intact during migration. The installer’s supported manual partition/encryption workflow and independent boot path must be rehearsed before this layout can be finalized. No encryption configuration is accepted merely by this draft.

Documentation recheck: Canonical's current installation guide describes the guided encryption choice after selecting whole-disk erase, while describing manual partitioning separately. It does not qualify encrypted manual installation into this populated SSD's proposed free space. Therefore the existing data-preserving encrypted layout remains a rehearsal requirement, not an executable installer recipe. The GNU removable GRUB path appeared in search results, but the full manual page fetch timed out; it is not accepted as proof of Ubuntu signed Secure Boot installation. Do not substitute an unverified boot command or erase the populated SSD to make the guided option available.

The external SSD must contain all Ubuntu boot files. Record the Dell internal ESP and Windows boot state before installation and prove they remain unchanged. Qualify the signed removable-media boot path with the intended Secure Boot configuration; do not disable Secure Boot as a shortcut. Acceptance requires booting the external SSD with the internal Windows boot disk unavailable to the boot path, then booting Windows independently with the SSD absent, and testing a second compatible x86-64 UEFI computer.

## Off-target backup contract

The internal Dell volume initially has about 234.1 GB free after preserving the optional flash model archive. SSD allocated usage is about 199.2 GB. These volume counters alone do not establish backup feasibility: the metadata inventory must finish, including logical file size, unreadable files, reparse entries and EFS-encrypted files. Preserve at least 32 GiB of internal free space as the working reserve; do not fill the Windows system volume to make a backup fit.

Before copying, freeze the agreed current release and stop only verified Prometheus writers using their normal pause/shutdown mechanism. Keep communication and security running. Capture a stable before/after inventory and application database consistency evidence. A live file copy without that boundary is not a consistent backup.

Back up all retained SSD data, releases, models, plans, native runtimes and recovery evidence into a new off-SSD destination. Application-only backups are insufficient. Preserve file data, alternate data streams and required metadata; classify reparse points rather than following them into another drive. Refuse unreadable/encrypted files without a documented recovery method. Record GPT/partition geometry separately without exporting credentials or private file listings to the public repository.

Verify every copied file against the frozen source using SHA-256 and size; record every exclusion explicitly. Perform an isolated restore of application state and representative large/model/native files, and verify the complete backup manifest is readable independently of the SSD. A successful copy count alone is not restore acceptance. Do not use mirroring/deletion flags against the backup destination.

If the available internal capacity cannot hold the selected backup plus reserve, preserve the inventory and resolve additional capacity before shrinking the SSD. Never solve a backup shortage by deleting unique current data or treating the flash as a full SSD backup.

## Installer-media preservation

The completed metadata inventory counted 409,545 SSD files totaling 197,759,035,853 logical bytes, with zero read errors, reparse entries or EFS-encrypted files. It explicitly excludes the recycle bin and System Volume Information. This was a live metadata inventory, not a consistent backup or content verification. Alternate data streams remain unmeasured. The internal volume had 234,059,898,880 bytes free: copying these logical bytes leaves only 1,941,124,659 bytes above the 32 GiB working reserve, before filesystem overhead, streams, temporary restore space and preservation of the remaining flash contents. Capacity is therefore not yet qualified. Do not begin a whole-drive backup or shrink based on the raw free-space comparison alone; measure the complete backup closure and use verified deduplication or additional off-target storage if necessary.

The recovery flash has approximately 19.5 GB free after verified archival of the optional Gemma model. Its remaining recovery material is still valuable. An Ubuntu ISO writer overwrites the flash’s filesystem; free space does not make the recovery files safe from that operation. Before repurposing it, create and verify a complete off-flash archive and test the retained recovery tools from that archive, or use separate empty installer media. Preserve a practical independent recovery path while the SSD is being changed.

The completed flash inventory adds 12,406,726,379 logical bytes. A subsequent full reconstruction/hash comparison proves that the existing internal archive matches seven SSD model blobs totaling 18,731,025,629 bytes. Binding and retaining that archive can reduce new-copy requirements; the capacity follow-up records an estimated 8,153,017,381 bytes above the 32 GiB reserve before overhead. Alternate-stream enumeration is in progress. No full backup or restore acceptance is implied by those measurements.

Download only the selected supported Ubuntu LTS image and verify its official checksum/signature with recorded identity before writing media. Linux application rehearsal must use an explicit platform overlay: the accepted Windows release bundles Windows-native cryptography/cffi and voice components and cannot simply become a Linux runtime by copying it.

## Exit criteria

1. Current companion migration/controller recovery and installed acceptance completed; physical reconnect and reboot behavior recorded.
2. Current Linux source/dependencies staged separately with exact artifact provenance; owner authentication, review/history/undo, model runtime and resource limits qualified. Unsupported features stay explicit.
3. Complete off-target backup and restore verification, capacity reserve, stable storage and finalized data-preserving geometry.
4. Preserved installer-media contents, verified image, live hardware trial and self-contained external boot.
5. Installed Ubuntu/Prometheus acceptance on the Dell and another compatible host, with Windows recovery retained.

Sources consulted: [Ubuntu installation](https://ubuntu.com/desktop/docs/en/latest/tutorial/install-ubuntu-desktop/), [USB creation](https://ubuntu.com/desktop/docs/en/latest/how-to/create-a-bootable-usb-stick/) and [advanced disk setup](https://ubuntu.com/desktop/docs/en/latest/reference/advanced-disk-setup-features/). Canonical identifies passphrase/LVM encryption as a supported recommended encryption option; that does not prove this proposed manual external-drive layout is already qualified.
