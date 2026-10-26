# Bounded full backup after migration

Status: execution plan only. No full copy, production stop, snapshot creation, or partition change was performed for this document. Execute after the bounded production migration has completed and its result is verified. This is a restorable file-level backup of the SSD and flash contents, with explicit system-directory exclusions; it is not a sector image or a claim that Windows boot state is recoverable from file copies alone.

## Scope and identity

Use a new create-only `Recovery-Archives/Prometheus-Full-Backup-<transaction>` directory on the internal NTFS volume. Resolve the recovery parent in the normal user profile, outside MSIX redirection. Keep the transaction plan, detailed source names, security descriptors, and restore mappings private. Public receipts contain counts, sizes, results, and digests.

| Role | Existing observed identity | Required execution binding |
|---|---|---|
| SSD source | `Prometheus-2TB`, volume size **1,950,372,327,424 bytes**, NTFS; previously E: | Exact volume GUID/serial, disk UniqueId/serial, partition GUID/offset/size, filesystem, and label/size. Resolve the current letter from this binding. |
| Flash source | `PROMETHEUS`, volume size **32,076,988,416 bytes**; previously D: | Same physical/volume identity binding, plus freshly observed filesystem and partition layout. Do not assume NTFS/ADS support. |
| Backup destination | Internal volume previously C:, containing the retained recovery archive | Exact volume GUID and physical disk identity, NTFS, writable generated destination, and physical path outside both source volumes. |

The current label-and-size guards identify the observed candidates, but are insufficient as permanent hardware bindings. A read-only `prepare` step must collect `Get-Volume`, `Get-Partition`, and `Get-Disk` identities, require one match per role, and seal them into the transaction manifest. Recheck these bindings before each copy batch and on every resume. Refuse a changed, missing, duplicate, or redirected volume. Preserve the observed GPT layout: SSD disk size 1,950,389,231,104 bytes, reserved partition at offset 17,408 (16,759,808 bytes), data partition at offset 16,777,216 (1,950,372,331,520 bytes). Store raw layout evidence privately; do not modify it.

## Capacity gate and existing archive

The inventories observed 409,545 SSD files / 197,759,035,853 logical bytes and 17,389 flash files / 12,406,726,379 bytes. Both reported zero enumeration errors, reparses, or EFS files. The completed SSD stream scan observed **4 named streams / 356 bytes**, including directory streams, with zero errors. These are preparation observations, not a consistent snapshot.

Seven SSD model blobs totaling **18,731,025,629 bytes** exactly match the retained `Flash-Gemma4-26b-20261022` chunk archive. Full archive reconstruction and current SSD hashes were checked, and an isolated disk restore independently verified all seven blobs plus the model manifest. The temporary restore copy was subsequently removed by the user; the original archive remains retained. Bind archive plan SHA-256 `9011dd7443ad9f962f95dd59089a0afe7e81fc626dab9dcdc5970f8ef393488b` and restore helper SHA-256 `7f3eb0796baf1c2949b2495ca7183d72ba1fd0f29a1d29143d093e6f97c9fc77`.

Reuse only those seven exact path/size/hash matches. Their backup manifest entries must reference the retained archive chunks and preserve their own source metadata/ADS. Never omit a file merely because its name resembles a digest. Include the archive plan, all chunk identities/hashes, relative archive location, model manifest, and restore instructions in the full backup's dependency manifest. Rehash/read back the required archive before final completion; do not delete or move it independently of this backup. Do not credit unproved SSD/flash duplicates.

After archive reuse, estimated additional default-stream data is **191,434,736,603 bytes**. Latest observed internal free space was **233,663,389,696 bytes**, leaving **7,868,914,725 bytes before overhead**, after preserving the mandatory **34,359,738,368-byte (32 GiB) reserve**. This is not yet a capacity approval.

At prepare time recompute free space and destination cluster size. Budget cluster-rounded file and stream allocations, directories, manifests/security descriptors, copy-tool temporary overhead, and any retained failed partial output. Count each logical file conservatively unless hard-link reuse is actually implemented and verified. The pending-file allocation is part of its planned payload, not a second full-file allowance. Before every batch require free bytes minus outstanding planned allocations and overhead to remain at least 32 GiB. Stop without deletion when that cannot be proved. Do not remove unrelated data or the verified archive to make room. Prefer small bounded batches and no unqualified compression assumption.

## Source consistency

Finish migration first. Then use the reviewed production ownership/lifecycle adapter to enter an explicitly recorded backup maintenance interval: suppress the owned restart path, flush and stop only captured authorized writers, and confirm the source trees have no cooperating application/model/controller writers. Record original task enabled/running states and exact process identities for restoration afterward. The generated migration fixture does not itself provide this production adapter. Do not use broad process-name termination.

The bounded default is a quiescent maintenance copy of both removable sources. A supported VSS snapshot is an alternative only if actually available and qualified for the relevant volume; do not presume the flash filesystem supports it. A VSS snapshot also does not substitute for application flush when application consistency matters. Do not begin a purported consistent full backup while writers remain active. Per-file before/after checks detect some changes but cannot establish cross-file consistency.

During copying capture volume-relative file ID, size, timestamps, attributes, named-stream set/sizes, and metadata before and after reading. Refuse/retry a changed item within the maintenance interval, preserving the failure receipt. Re-enumerate the full trees at the end and require identical path/type/stream membership and stable recorded source state. Any access error or changed source prevents the complete marker. Restore the original production state through the same reviewed adapter, including on failure; report restoration failure separately from backup verification.

## Copy, resume, and metadata

Implement one executor with `prepare`, `copy --resume`, `verify`, and `restore-check` modes. Preparation creates the transaction plan and exact destination ownership marker. Resume must verify the plan, source/destination identities, path containment, and journal; it must not adopt an arbitrary existing directory. Never use `/MIR`, `/PURGE`, source deletion, recursive reparse traversal, or destructive destination replacement outside this owned root.

The existing isolated native qualification passed two-way copying using `robocopy /E /COPY:DATS /DCOPY:DAT /R:0 /W:0 /XJ`, including a default stream, file ADS, directory ADS, DACL, mtime, and an empty directory. Use that tested copy behavior as the baseline. Before full execution, extend the small fixture to verify exact full-path exclusion of the seven reused blobs and resumable repair of one corrupted destination file. Do not introduce `/Z`, backup privileges, or additional copy flags without a targeted fixture for their effects.

Copy into separate `ssd/tree` and `flash/tree` destinations in bounded directory batches. Include empty directories and source-root metadata/ADS. Preserve and independently verify default streams and every named file/directory stream. Collect owner/group and DACL descriptors in the private restore manifest; DATS only demonstrates DACL copying, not owner or SACL restoration. Record timestamps and attributes explicitly. Treat unreadable security metadata, new EFS files, reparses, or unsupported stream types as explicit blockers/coverage exceptions, never silently skipped data. If exact owner/audit restoration is required, qualify the necessary restore privilege and SACL capture before claiming that coverage. Preserve hard-link relationships by source volume/file ID in the manifest, without assuming their bytes were deduplicated for capacity.

After each batch, read source data and destination data independently and compare SHA-256 plus length for each stream. Do not use timestamp/size equality as completed-copy authority. A durable journal entry records verified source identity, all stream digests, metadata, and destination readback, then flushes before publishing completion for that item. Resume revalidates completed entries and repairs only explicitly owned failed outputs; it must not trust Robocopy's skip decision for a same-size corrupt destination. Retain failures and account for their disk use. Final directory timestamps/metadata are restored after child copies.

## Restore manifest and acceptance

The final private manifest maps every included volume-relative path and entry type to stored streams or an exact archive dependency, with size/hash, timestamps, attributes, security descriptors, and hard-link group. Record filesystem and original partition layout separately. List `$RECYCLE.BIN` and `System Volume Information` as explicit excluded system directories; this backup must not imply that their contents were copied. Include empty directories, root streams, all seven model-archive references, archive reconstruction instructions, transaction/source identities, tool/source hashes, and verification receipts.

Publish a complete marker only after: all source entries reconcile; all destination streams are independently read back; all archive dependencies reverify; no unresolved copy/metadata errors remain; the reserve still holds; and a generated isolated restore sample reconstructs ordinary files, empty directories, file/directory ADS, metadata, and one archive-referenced model entry. The prior full seven-blob restore proof remains attached. Restore into a new scratch root only; never test by overwriting production. Publish a digest of the final manifest and retain a second copy of the small manifest/restore instructions outside the internal backup directory. Do not claim full backup completion from a Robocopy exit code alone.

## Remaining prerequisites; next executable step

1. Complete and verify production migration, then supply its reviewed exact-owner maintenance/restore adapter. No production stop is part of this plan-writing task.
2. Capture and pin the three exact physical/volume identities; establish the flash filesystem's stream/metadata capabilities and refresh capacity including allocation overhead. The current margin is provisional.
3. Implement the bounded executor above and run the two additional generated copy fixtures plus interrupted-journal/resume verification. Existing inventory scripts are not that executor.
4. Enter the recorded quiescent interval and execute copy/readback only when the preceding gates pass. Preserve all preparation/failure evidence and report any concrete failing gate; do not start another open-ended design cycle.
