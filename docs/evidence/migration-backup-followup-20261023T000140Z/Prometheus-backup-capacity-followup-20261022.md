# Ubuntu backup preparation: verified reusable model archive

The existing internal-drive archive reconstructs all seven model blobs byte-for-byte and matches the current SSD source hashes: 18,731,025,629 bytes. This was a read-only comparison. The archive must remain intact and be explicitly referenced by the future full-backup manifest. No model was removed from the SSD.

The metadata inventories found 197,759,035,853 logical bytes on the SSD and 12,406,726,379 on the recovery flash, totaling 210,165,762,232. The observed internal free space was 233,947,492,352 bytes. Separate copying left a 10,578,008,248-byte shortfall against the proposed 32 GiB working reserve, before overhead. Reusing the verified archive instead of duplicating those model bytes reduces estimated new data to 191,434,736,603 bytes and leaves an estimated 8,153,017,381 bytes above that reserve, before streams, metadata, changing files and restore workspace. These are planning figures, not final capacity qualification.

Next backup gates: inventory alternate streams and required metadata; select a recoverable backup format; bind the retained model archive and its restoration mapping; establish a consistent stopped-writer snapshot; recheck free space; copy and hash-read back; restore representative data and verify metadata. A full model restoration to disk and a complete SSD backup have not run. Do not partition or format either drive based on the streaming comparison alone.

Windows denied the read-only supported-partition-size query. The exact shrink boundary remains unknown; the existing external EFI/Linux layout is only a proposal. No partition, boot, encryption or firmware changes occurred. The recovery flash still holds required recovery material.

Migration qualification remains ahead of Ubuntu: the generated filesystem store passed 47 frozen tests, eight independent regression cases and six overlapping interruption reruns. The end-to-end generated Windows task/provider test is in implementation. Installed core dev55 and companion R5 remain unchanged, and physical reconnect/reboot acceptance remains pending. Routine display updates remain off.
