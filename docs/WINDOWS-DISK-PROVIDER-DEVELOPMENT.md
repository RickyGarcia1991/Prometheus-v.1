# Windows disk observations — combined dev29 candidate

The raw provider is connected through `DiskPressureProbe` to the Windows
Interface resource dashboard for its configured data volume. A fixed hidden
direct-interpreter child performs bounded native reads; cached and coalesced
requests avoid duplicate work. Raw provider details follow below.

The dashboard shows observation age, whole-device scope, busy time, queue,
transfers and interval latency where supported. These include other applications.
Unavailable, warming, stale and no-transfer observations remain explicit. The
observer is excluded from approval, reservation, workload history and workers;
it does not establish calibrated pressure, remaining runtime or per-job cost.
No stress workload, counter enablement or persistent sampling thread is added.
Selected-project volume display is implemented in subsequent development; see
[Selected-project observations](SELECTED-PROJECT-DISK-DEVELOPMENT.md) for its
qualification and remaining unsupported topology. The signed installed dev29
release still displays its configured data volume only.

## Contract

`collect_disk_observations(roots)` accepts a dictionary with required `data`
and optional `project` keys. Values are existing local, drive-qualified absolute
directories selected by the trusted app. The app must not expose this argument
as an arbitrary model command or an unauthenticated request. UNC, device paths,
relative paths, parent traversal, alternate streams and wildcard paths are
rejected. The provider reads directory metadata, not directory contents.

The fixed helper entry point is:

```text
<configured-python> -B -m prometheus_assistant.windows_disk_observations
```

It takes no arguments and reads at most 8,193 bytes from stdin, accepting at most
8,192. Example input (the caller substitutes its actual selected roots):

```json
{"schema_version":1,"roots":{"data":"E:/","project":"C:/"}}
```

Only these two top-level fields are accepted. Duplicate JSON keys, non-finite
constants, malformed input, unknown fields and other schema versions are
rejected. It emits one JSON line, capped at 32 KiB, with no raw path, drive
letter, volume GUID, device GUID, serial, native exception text or traceback.
Helper exit zero means that the observation protocol completed; callers must
inspect the JSON status. An unavailable observation is a normal protocol result.

The output schema is version 1:

- `status`: `raw`, `partial` or `unavailable`. These are observation states;
  none means healthy, approved or safe to schedule. `reason_code` is null for
  complete raw observations, otherwise a fixed code.
- `source`: `windows_pdh_raw`; `scope`: `device`.
- `volumes`: up to two entries with `role`, status/reason, and, when mapped,
  opaque SHA-256 `volume_id`, `device_id` and `mapping_id` values.
- `devices`: up to two deduplicated device entries with `device_id`,
  `identity_basis`, status, and the four fixed `counters` keys below.
- Each counter is unavailable with a reason, or raw with `cstatus`,
  `timestamp_100ns`, `first`, `second`, `multi_count`, `counter_type`,
  `time_base`, `scale`, `default_scale` and opaque `instance_id`.

`time_base` is null for the instantaneous queue counter. Its raw count does not
need a time base, and the provider does not request one. The three time-based
counters require a valid native time base.

The raw fields retain PDH meanings. `timestamp_100ns` is the native raw sample
timestamp, not an elapsed interval or a promise of freshness. Raw `first` and
`second` are counter-type-dependent values. In particular they must not be
displayed as a precomputed rate. No first-sample rate, busy percentage, latency,
calibrated threshold or estimated job I/O is manufactured. Process transfer
counters include operations beyond physical disk I/O and are not substituted.

## Identity qualification and native behavior

Each root is resolved through `GetVolumePathNameW` and
`GetVolumeNameForVolumeMountPointW`. Windows follows junctions to the actual
volume. Local drive type and directory existence are checked. Short-lived
volume and physical-device handles use desired access zero, read/write/delete
sharing, `OPEN_EXISTING` and no inheritable security attributes.

Only these metadata IOCTLs are permitted:

- `IOCTL_VOLUME_GET_VOLUME_DISK_EXTENTS`: exactly one valid extent.
- `IOCTL_STORAGE_GET_DEVICE_NUMBER_EX`: matching device number, hardware GUID
  and flags from the volume and corresponding physical-device handle. A volume
  must identify a partition and the physical handle must identify the whole
  disk. Missing GUIDs, random/conflicting identity flags, unknown flags and
  malformed/version-mismatched responses remain unavailable. The page-83 flag
  is supported.
- `IOCTL_STORAGE_QUERY_PROPERTY`: fixed `StorageDeviceProperty` /
  `PropertyStandardQuery` request. Only bounded descriptor metadata is parsed;
  serial strings are not decoded or returned.

The conservative supported bus set is ATA, IEEE-1394, USB, SAS, SATA, SD, MMC,
NVMe and UFS. Unknown, generic SCSI, RAID, remote/network, virtual and Storage
Spaces reports remain unsupported. This identifies the device Windows exposes;
it is not an attestation about a USB bridge or physical hardware. Broadening
support requires explicit topology evidence, not relabeling unavailable values.

`device_id` derives from the qualified hardware GUID. `volume_id` derives from
the volume GUID. `mapping_id` includes both plus current disk number, partition,
extent and bus/identity flags. Disk number and PDH instance name alone are never
treated as persistent identity. Every successfully mapped root is resolved again
after collection. A changed/disconnected mapping loses its observations. Two
roots sharing a disk are sampled once, while conflicting GUID/number pairs
cannot select a counter instance. Hashes reduce identifier exposure but are not
secret authentication tokens or anonymous identity guarantees.

One PDH query adds only these English wildcard counter paths:

| Output key | PhysicalDisk counter | Required counter type |
| --- | --- | --- |
| `latency` | `Avg. Disk sec/Transfer` | 805438464 |
| `idle` | `% Idle Time` | 542573824 |
| `queue` | `Current Disk Queue Length` | 65536 |
| `transfers` | `Disk Transfers/sec` | 272696320 |

`PdhAddEnglishCounterW` supports language-neutral lookup. One
`PdhCollectQueryData` call is followed by `PdhGetRawCounterArrayW` for wildcard
instances. Only a unique recognized instance whose disk number matches the
qualified mapping is accepted. `_Total` is never a fallback. Per-value CStatus
must be VALID_DATA or NEW_DATA even if adding/collecting succeeded. Type,
time-base, scale, timestamp, sign and count checks reject malformed samples.
One counter can be unavailable while another remains qualified.

Counter buffers are capped at 64 KiB and 64 instances, with bounded UTF-16 names
whose pointers must lie within the returned buffer. The size/read pair has no
growth-retry loop. Descriptor buffers are capped at 4 KiB, extent/device-number
buffers at their fixed structure sizes. Handles are closed in `finally` paths;
a close failure prevents a successful result. There is no persistent handle,
service, timer, polling thread, model call, network access, counter repair or
settings mutation. `IOCTL_DISK_PERFORMANCE`, its OFF counterpart and `diskperf`
are deliberately absent: collecting with that IOCTL can enable statistics.

## Parent integration still required

Native DLL calls are synchronous and have no usable Python deadline. The future
caller must run the fixed helper with trusted configured Python/module search
paths, a hidden window, no shell, bounded pipe reads and an owned process deadline.
A two-second deadline was used in the native smoke below. Timeout handling must
terminate and reap only that child. The module itself does not claim to enforce
wall-clock time. Run it outside the UI/approval/scheduler lock.

The future coordinator still needs result-schema validation, monotonic sampling
intervals, first-sample warming state, paired counter calculations, missing/reset
and sleep-gap handling, demand cache/coalescing/backoff, freshness display and
overhead measurement. Any interval must bind the unchanged mapping, device and
counter instance/type/timebase. No admission, job attribution or remote behavior
should be changed by integrating this observation source.

## Verification evidence

2026-10-15: the focused fake-native/real-parser suite passed **108 tests**. It
covers zero-access handles and cleanup, single-extent and hardware identity,
unsupported buses, identity changes and conflicting mappings, exact instance
selection, missing/invalid PDH values, buffer/pointer/count bounds, size races,
partial results, duplicate roots, opaque output and the fixed helper protocol.
These tests invoke neither live models nor remote jobs and create no disk stress.

Command, from the authoritative source directory:

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
python -B -m pytest tests/test_windows_disk_observations.py -p no:cacheprovider --basetemp ./disk-provider-tests -q
```

One native smoke ran the new fixed module on the normal host account, in a
hidden child with a two-second parent timeout. It finished in **0.313 seconds**,
exit code 0, with empty stderr. It used no UAC or Windows settings changes.
The selected E: root resolved a single device and stable hardware identity
before and after collection. All four raw counters passed validation, with
instantaneous queue 0 in that sample. The selected C: root reported
`topology_unsupported` under this deliberately narrow contract; overall result
was `partial`. No cause for C:'s unsupported topology was inferred, and no access
or topology rules were loosened to obtain a full result.

Independent review then corrected a portability issue: the instantaneous queue
must not require a successful time-base lookup. The successful native sample
preceded this correction (the host happened to supply a time base). The exact
pre-correction result is retained in task output
`Prometheus-windows-disk-provider-native.json`; the correction has dedicated
fake-native contracts. A justified post-fix read then completed in **0.312
seconds**, exit code 0, with empty stderr and the same two-second boundary. All
four SSD counters remained valid, with queue `time_base: null`; the other three
time bases remained 10,000,000. C: remained `topology_unsupported`. The artifact
retains both complete results and the exact process boundary.

This smoke proves one ordinary-account read on this host and attached SSD. It
does not verify sustained latency/CPU/RAM overhead, physical unplug/replug,
system sleep, other storage hardware/locales/Windows versions, rate accuracy,
timeout termination, UI display, parent caching or production packaging. The
fake-native contracts cover several of these failure shapes but are not a
substitute for their later integration checks. Earlier sandbox counter failures
do not represent ordinary Windows-account capability.

## Primary references

- [Volume resolution](https://learn.microsoft.com/en-us/windows/win32/api/fileapi/nf-fileapi-getvolumepathnamew) and [volume GUID lookup](https://learn.microsoft.com/en-us/windows/win32/api/fileapi/nf-fileapi-getvolumenameforvolumemountpointw).
- [Metadata handles](https://learn.microsoft.com/en-us/windows/win32/api/fileapi/nf-fileapi-createfilew), [volume extents](https://learn.microsoft.com/en-us/windows/win32/api/winioctl/ni-winioctl-ioctl_volume_get_volume_disk_extents), [extended device identity](https://learn.microsoft.com/en-us/windows-hardware/drivers/ddi/ntddstor/ns-ntddstor-_storage_device_number_ex), and [Microsoft SDK identity flag definitions](https://github.com/microsoft/win32metadata/blob/main/generation/WinSDK/RecompiledIdlHeaders/shared/ntddstor.h).
- [Storage descriptor](https://learn.microsoft.com/en-us/windows-hardware/drivers/ddi/ntddstor/ns-ntddstor-_storage_device_descriptor) and [bus enumeration](https://learn.microsoft.com/en-us/windows/win32/api/winioctl/ne-winioctl-storage_bus_type).
- [English counters](https://learn.microsoft.com/en-us/windows/win32/api/pdh/nf-pdh-pdhaddenglishcounterw), [collection](https://learn.microsoft.com/en-us/windows/desktop/api/pdh/nf-pdh-pdhcollectquerydata), [raw arrays](https://learn.microsoft.com/en-us/windows/win32/api/pdh/nf-pdh-pdhgetrawcounterarrayw), [counter metadata](https://learn.microsoft.com/en-us/windows/win32/api/pdh/ns-pdh-pdh_counter_info_w), and [PhysicalDisk counter semantics](https://learn.microsoft.com/en-us/previous-versions/aa394308%28v%3Dvs.85%29).
- [Time-base applicability](https://learn.microsoft.com/en-us/windows/win32/api/pdh/nf-pdh-pdhgetcountertimebase).
- [Why the disk-performance IOCTL is excluded](https://learn.microsoft.com/en-us/windows-hardware/drivers/ddi/ntdddisk/ni-ntdddisk-ioctl_disk_performance).
