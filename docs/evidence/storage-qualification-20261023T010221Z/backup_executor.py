"""Bounded Windows backup prepare/fixture engine; production maintenance is not wired.

Default streams, named file/directory streams, creation/modified times, attributes,
and owner/group/DACL evidence are in scope. SACL/boot-sector imaging is not claimed.
"""
import argparse
import ctypes as c
from ctypes import wintypes as w
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import stat
import subprocess
import tempfile
import uuid

RESERVE = 32 * 1024 ** 3
PREFIX = 'prometheus-full-backup-fixture-'


class Refusal(ValueError):
    pass


def encode(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def physical(path):
    path = Path(os.path.abspath(path))
    for node in (path, *path.parents):
        if node.exists() or node.is_symlink():
            if node.lstat().st_file_attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT:
                raise Refusal('Reparse path refused')
    return path


def create(path, data):
    with physical(path).open('xb') as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())


def relative(root, name):
    if not isinstance(name, str) or '\\' in name or ':' in name:
        raise Refusal('Noncanonical relative path')
    parts = name.split('/')
    if name != '.' and (not name or any(p in ('', '.', '..') for p in parts)):
        raise Refusal('Relative path escape')
    target = physical(root if name == '.' else root.joinpath(*parts))
    target.relative_to(root)
    return target


class StreamData(c.Structure):
    _fields_ = [('size', c.c_longlong), ('name', w.WCHAR * 296)]


K = c.WinDLL('kernel32', use_last_error=True)
A = c.WinDLL('advapi32', use_last_error=True)
K.FindFirstStreamW.argtypes = [w.LPCWSTR, w.DWORD, c.POINTER(StreamData), w.DWORD]
K.FindFirstStreamW.restype = w.HANDLE
K.FindNextStreamW.argtypes = [w.HANDLE, c.POINTER(StreamData)]
K.FindNextStreamW.restype = w.BOOL
K.FindClose.argtypes = [w.HANDLE]
K.FindClose.restype = w.BOOL
K.LocalFree.argtypes = [w.HLOCAL]
K.LocalFree.restype = w.HLOCAL
K.GetCompressedFileSizeW.argtypes = [w.LPCWSTR, c.POINTER(w.DWORD)]
K.GetCompressedFileSizeW.restype = w.DWORD
K.GetDiskFreeSpaceW.argtypes = [w.LPCWSTR, c.POINTER(w.DWORD), c.POINTER(w.DWORD), c.POINTER(w.DWORD), c.POINTER(w.DWORD)]
K.GetDiskFreeSpaceW.restype = w.BOOL
K.GetVolumePathNameW.argtypes = [w.LPCWSTR, w.LPWSTR, w.DWORD]
K.GetVolumePathNameW.restype = w.BOOL
K.GetVolumeInformationW.argtypes = [w.LPCWSTR, w.LPWSTR, w.DWORD, c.POINTER(w.DWORD), c.POINTER(w.DWORD), c.POINTER(w.DWORD), w.LPWSTR, w.DWORD]
K.GetVolumeInformationW.restype = w.BOOL
A.GetFileSecurityW.argtypes = [w.LPCWSTR, w.DWORD, w.LPVOID, w.DWORD, c.POINTER(w.DWORD)]
A.GetFileSecurityW.restype = w.BOOL
A.ConvertSecurityDescriptorToStringSecurityDescriptorW.argtypes = [w.LPVOID, w.DWORD, w.DWORD, c.POINTER(w.LPWSTR), c.POINTER(w.DWORD)]
A.ConvertSecurityDescriptorToStringSecurityDescriptorW.restype = w.BOOL


def security(path):
    needed = w.DWORD()
    A.GetFileSecurityW(str(path), 7, None, 0, c.byref(needed))
    if c.get_last_error() != 122 or not needed.value:
        raise c.WinError(c.get_last_error())
    data = c.create_string_buffer(needed.value)
    if not A.GetFileSecurityW(str(path), 7, data, len(data), c.byref(needed)):
        raise c.WinError(c.get_last_error())
    result = w.LPWSTR()
    if not A.ConvertSecurityDescriptorToStringSecurityDescriptorW(data, 1, 7, c.byref(result), None):
        raise c.WinError(c.get_last_error())
    try:
        return result.value
    finally:
        K.LocalFree(c.cast(result, w.HLOCAL))


def capability_policy(filesystem, flags):
    if filesystem == 'NTFS' and flags & 0x40000 and flags & 8:
        return dict(filesystem='NTFS', named_streams=True, persistent_acls=True)
    if filesystem == 'FAT32' and not flags & (0x40000 | 8):
        return dict(filesystem='FAT32', named_streams=False, persistent_acls=False)
    raise Refusal('Unsupported or inconsistent filesystem capabilities')


def capabilities(path):
    volume = c.create_unicode_buffer(32768)
    if not K.GetVolumePathNameW(str(physical(path)), volume, len(volume)):
        raise c.WinError(c.get_last_error())
    serial, maximum, flags = w.DWORD(), w.DWORD(), w.DWORD()
    filesystem = c.create_unicode_buffer(64)
    if not K.GetVolumeInformationW(volume.value, None, 0, c.byref(serial), c.byref(maximum), c.byref(flags), filesystem, len(filesystem)):
        raise c.WinError(c.get_last_error())
    return dict(**capability_policy(filesystem.value, flags.value), volume_serial=serial.value)


def ntfs_streams(path):
    data = StreamData()
    handle = K.FindFirstStreamW(str(path), 0, c.byref(data), 0)
    if handle == c.c_void_p(-1).value:
        if c.get_last_error() == 38:
            return []
        raise c.WinError(c.get_last_error())
    rows = []
    try:
        while True:
            name = data.name
            stream_path = path if name == '::$DATA' else Path(str(path) + name)
            rows.append(dict(name=name, bytes=data.size, sha256=sha(stream_path)))
            if not K.FindNextStreamW(handle, c.byref(data)):
                if c.get_last_error() != 38:
                    raise c.WinError(c.get_last_error())
                return sorted(rows, key=lambda r: r['name'])
    finally:
        if not K.FindClose(handle):
            raise c.WinError(c.get_last_error())


def streams(path):
    capability = capabilities(path)
    if capability['filesystem'] == 'FAT32':
        return [] if path.is_dir() else [dict(name='::$DATA', bytes=path.stat().st_size, sha256=sha(path))]
    # NTFS enumeration errors are never converted to a no-ADS fallback.
    return ntfs_streams(path)


def entry(path, root, metadata_policy=None):
    path = physical(path)
    before = path.stat()
    if before.st_file_attributes & stat.FILE_ATTRIBUTE_ENCRYPTED:
        raise Refusal('EFS needs separately qualified preservation')
    capability = capabilities(path)
    policy = metadata_policy or capability['filesystem']
    if policy not in ('NTFS', 'FAT32') or (policy == 'NTFS' and not capability['persistent_acls']):
        raise Refusal('Destination cannot preserve source metadata policy')
    modified = before.st_mtime_ns
    created = getattr(before, 'st_birthtime_ns', before.st_ctime_ns)
    if policy == 'FAT32':
        # FAT last-write resolution is 2 seconds; creation subsecond unit is 10 ms.
        modified = modified // 2_000_000_000 * 2_000_000_000
        created = created // 10_000_000 * 10_000_000
    row = dict(path=path.relative_to(root).as_posix(), kind='directory' if path.is_dir() else 'file',
               modified_ns=modified, created_ns=created, metadata_policy=policy,
               attributes=before.st_file_attributes, security=security(path) if policy == 'NTFS' else None,
               streams=streams(path))
    after = path.stat()
    if (before.st_ino, before.st_size, before.st_mtime_ns) != (after.st_ino, after.st_size, after.st_mtime_ns):
        raise Refusal('Source changed during read')
    return row


def inventory(root, metadata_policy=None):
    root = physical(root)
    paths = [root]
    def inaccessible(error):
        raise error
    for directory, dirs, files in os.walk(root, followlinks=False, onerror=inaccessible):
        for name in dirs + files:
            paths.append(physical(Path(directory) / name))
    return [entry(path, root, metadata_policy) for path in sorted(paths)]


def cluster_size(path):
    values = [w.DWORD() for _ in range(4)]
    if not K.GetDiskFreeSpaceW(str(physical(path).anchor), *(c.byref(v) for v in values)):
        raise c.WinError(c.get_last_error())
    return values[0].value * values[1].value


def allocated(path):
    high = w.DWORD()
    c.set_last_error(0)
    low = K.GetCompressedFileSizeW(str(path), c.byref(high))
    if low == 0xffffffff and c.get_last_error():
        raise c.WinError(c.get_last_error())
    return (high.value << 32) | low


def required_remaining(plan, tree):
    cluster = cluster_size(tree.parent)
    needed = 0
    for row in plan['entries']:
        if row['path'] in plan['references']:
            continue
        path = relative(tree, row['path'])
        present = {s['name'] for s in streams(path)} if path.exists() else set()
        for stream in row['streams']:
            rounded = ((stream['bytes'] + cluster - 1) // cluster) * cluster
            stream_path = path if stream['name'] == '::$DATA' else Path(str(path) + stream['name'])
            occupied = allocated(stream_path) if stream['name'] in present else 0
            needed += max(0, rounded - occupied)
    # No credit for unverified directory/metadata allocation or extra destination streams.
    return needed + len(plan['entries']) * max(cluster, 8192) + 1024 * 1024


def check_reserve(required, reserve=RESERVE, free=None):
    if reserve < RESERVE:
        raise Refusal('32 GiB reserve cannot be lowered')
    if free is None or free - required < reserve:
        raise Refusal('Insufficient space after reserve')


def prepare(identity_file, expected_hash, output):
    """Validate the pinned read-only identity artifact; create a non-executable draft."""
    raw = Path(identity_file).read_bytes()
    if hashlib.sha256(raw).hexdigest() != expected_hash:
        raise Refusal('Identity snapshot pin mismatch')
    snapshot = json.loads(raw)
    if snapshot.get('ReadOnly') is not True or not snapshot.get('ObservedUtc'):
        raise Refusal('Read-only dated identity snapshot required')
    targets = snapshot.get('Targets', [])
    expected = {'OS': (493705228288, 'NTFS'), 'Prometheus-2TB': (1950372327424, 'NTFS'),
                'PROMETHEUS': (32076988416, 'FAT32')}
    if len(targets) != 3 or {t['Label'] for t in targets} != set(expected):
        raise Refusal('Exact three target roles required')
    for target in targets:
        volume, partition, disk = target['Volume'], target['Partition'], target['Disk']
        if (volume['Size'], volume['FileSystem']) != expected[target['Label']] or volume['HealthStatus'] != 'Healthy':
            raise Refusal('Expected volume geometry/filesystem/health changed')
        if not volume['UniqueId'] or not disk['UniqueId'] or not disk['SerialNumber'] or partition['DiskNumber'] != disk['Number']:
            raise Refusal('Incomplete physical identity binding')
        if disk['PartitionStyle'] == 'GPT' and not partition['Guid']:
            raise Refusal('GPT partition identity absent')
    if len({t['Disk']['UniqueId'] for t in targets}) != 3:
        raise Refusal('Source and destination physical disks must differ')
    draft = dict(schema=1, identity_sha256=expected_hash, identity_observed_utc=snapshot['ObservedUtc'],
                 targets=targets, reserve_bytes=RESERVE, execution_allowed=False,
                 production_copy_available=False,
                 requirements=['Fresh live identity revalidation by production integration',
                               'Authenticated quiescent maintenance receipt from reviewed production adapter',
                               'Fresh cluster-rounded inventory and resolved per-filesystem metadata coverage'],
                 metadata_policy={'NTFS': 'default/named streams, times, attributes, owner/group/DACL; no SACL image claim',
                                  'FAT32': 'default streams, supported FAT timestamps and attributes; ACL/ADS not filesystem capabilities'})
    create(output, encode(draft))
    return dict(prepared=True, execution_allowed=False, identity_sha256=expected_hash,
                draft_sha256=sha(output), reserve_bytes=RESERVE)


def prepare_fixture(source, destination, references=None):
    """All mutations confined to a fresh direct temporary fixture descendant."""
    source, destination = physical(source), physical(destination)
    fixture = source.parent
    if fixture.parent != physical(tempfile.gettempdir()) or not fixture.name.startswith(PREFIX):
        raise Refusal('Generated temporary fixture required')
    if source.name != 'source' or destination != fixture / 'backup' or destination.exists():
        raise Refusal('Exact fresh fixture output required')
    rows = inventory(source)
    references = references or {}
    by_path = {row['path']: row for row in rows}
    for name, ref in references.items():
        relative(source, name)
        row = by_path.get(name)
        if not row or row['kind'] != 'file' or set(ref) != {'path', 'sha256', 'bytes'}:
            raise Refusal('Exact file reference required')
        external = physical(ref['path'])
        external.relative_to(fixture)
        if external.is_relative_to(source) or external.is_relative_to(destination):
            raise Refusal('Archive must be separate from source/output')
        default = next(s for s in row['streams'] if s['name'] == '::$DATA')
        # This milestone does not discard ADS on archive-referenced entries.
        if len(row['streams']) != 1 or default['sha256'] != ref['sha256'] or default['bytes'] != ref['bytes'] or external.stat().st_size != ref['bytes'] or sha(external) != ref['sha256']:
            raise Refusal('Archive reference/stream closure mismatch')
    planned = sum(s['bytes'] for row in rows if row['path'] not in references for s in row['streams'])
    # Conservative fixed fixture overhead; real prepare must use cluster-rounded inventory.
    check_reserve(planned + len(rows) * 8192 + 1024 * 1024, free=shutil.disk_usage(fixture).free)
    plan = dict(schema=1, mode='fixture', transaction=uuid.uuid4().hex, source=str(source),
                destination=str(destination), reserve_bytes=RESERVE, entries=rows,
                metadata_policy=capabilities(source)['filesystem'],
                references=references, production_copy_available=False,
                metadata_scope='streams, creation/modified times, attributes, owner/group/DACL; no SACL or disk image')
    destination.mkdir()
    create(destination / 'plan.json', encode(plan))
    create(destination / 'ownership.json', encode(dict(transaction=plan['transaction'], plan_sha256=sha(destination / 'plan.json'))))
    return destination


def load_plan(destination):
    destination = physical(destination)
    raw = (destination / 'plan.json').read_bytes()
    plan = json.loads(raw)
    owner = json.loads((destination / 'ownership.json').read_bytes())
    if raw != encode(plan) or owner != dict(transaction=plan['transaction'], plan_sha256=hashlib.sha256(raw).hexdigest()) or plan['destination'] != str(destination) or plan['mode'] != 'fixture':
        raise Refusal('Output plan/ownership mismatch')
    if destination.parent.parent != physical(tempfile.gettempdir()) or not destination.parent.name.startswith(PREFIX):
        raise Refusal('Fixture destination scope')
    if physical(plan['source']) != destination.parent / 'source':
        raise Refusal('Fixture source binding')
    return plan


def robocopy(source, target, exclusions=(), filename=None):
    args = [str(Path(os.environ['WINDIR']) / 'System32/robocopy.exe'), str(source), str(target)]
    if filename:
        args += [filename, '/IS', '/IT']
    else:
        args += ['/E']
    args += ['/COPY:DATS', '/DCOPY:DAT', '/R:0', '/W:0', '/XJ', '/NP', '/NFL', '/NDL', '/NJH', '/NJS']
    if exclusions:
        args += ['/XF', *map(str, exclusions)]
    result = subprocess.run(args, capture_output=True, timeout=120, creationflags=subprocess.CREATE_NO_WINDOW)
    if result.returncode >= 8:
        raise Refusal('Robocopy failed; no complete receipt')


def copy_fixture(destination, fault=lambda point: None):
    plan = load_plan(destination)
    source, destination = physical(plan['source']), physical(destination)
    tree = destination / 'tree'
    expected = {row['path']: row for row in plan['entries']}
    if inventory(source) != plan['entries']:
        raise Refusal('Source changed since preparation')
    if tree.exists():
        extra = set(row['path'] for row in inventory(tree, plan['metadata_policy'])) - (set(expected) - set(plan['references']))
        if extra:
            raise Refusal('Foreign output entry preserved')
    check_reserve(required_remaining(plan, tree), free=shutil.disk_usage(destination).free)
    db = sqlite3.connect(destination / 'journal.sqlite')
    try:
        db.execute('PRAGMA synchronous=FULL')
        db.execute('CREATE TABLE IF NOT EXISTS state (key TEXT PRIMARY KEY, value TEXT NOT NULL)')
        pin = hashlib.sha256(encode(plan)).hexdigest()
        stored = db.execute("SELECT value FROM state WHERE key='plan'").fetchone()
        if stored and stored[0] != pin:
            raise Refusal('Journal plan mismatch')
        db.execute("INSERT OR IGNORE INTO state VALUES ('plan', ?)", (pin,))
        db.execute("INSERT OR REPLACE INTO state VALUES ('phase', 'copy-intent')")
        db.commit()
        final = destination / 'verified.json'
        if final.exists():
            previous = json.loads(final.read_bytes())
            if previous.get('plan_sha256') != pin or previous.get('fixture_only') is not True:
                raise Refusal('Foreign completion receipt')
            # Retain prior proof as history, never leave it published during repair.
            os.rename(final, destination / ('previous-verified-' + uuid.uuid4().hex + '.json'))
        fault('after-intent')
        exclusions = [relative(source, name) for name in plan['references']]
        robocopy(source, tree, exclusions)
        fault('after-copy')
        for name, row in expected.items():
            if name in plan['references']:
                ref = plan['references'][name]
                external = physical(ref['path'])
                if external.stat().st_size != ref['bytes'] or sha(external) != ref['sha256']:
                    raise Refusal('Archive dependency changed')
                continue
            target = relative(tree, name)
            if entry(target, tree, plan['metadata_policy']) != row and row['kind'] == 'file':
                original = relative(source, name)
                robocopy(original.parent, target.parent, filename=original.name)
            if entry(target, tree, plan['metadata_policy']) != row:
                raise Refusal('Destination bytes/metadata readback mismatch')
        # Repairing a child can change directory timestamps; reapply directory metadata.
        robocopy(source, tree, exclusions)
        actual = inventory(tree, plan['metadata_policy'])
        wanted = [row for row in plan['entries'] if row['path'] not in plan['references']]
        if actual != wanted or inventory(source) != plan['entries']:
            raise Refusal('Final source/destination reconciliation failed')
        fault('before-complete')
        db.execute("INSERT OR REPLACE INTO state VALUES ('phase', 'verified')")
        db.commit()
        report = dict(passed=True, fixture_only=True, production_backup_complete=False,
                      entries=len(wanted), archive_references=len(plan['references']),
                      plan_sha256=pin, reserve_bytes=RESERVE)
        if final.exists():
            if final.read_bytes() != encode(report):
                raise Refusal('Foreign completion receipt')
        else:
            create(final, encode(report))
        return report
    finally:
        db.close()


def validate_full_prerequisites(identity_file, expected_hash, maintenance, metadata_complete, free, required):
    """Read-only integration gate. Snapshot identities alone never authorize copying."""
    if sha(identity_file) != expected_hash:
        raise Refusal('Identity snapshot pin mismatch')
    identities = json.loads(Path(identity_file).read_bytes())
    if identities.get('ReadOnly') is not True or not identities.get('Targets'):
        raise Refusal('Exact identity snapshot required')
    if not maintenance or maintenance.get('verified_by_production_adapter') is not True:
        raise Refusal('Reviewed fresh quiescent maintenance receipt required')
    if not metadata_complete:
        raise Refusal('Unresolved metadata coverage')
    check_reserve(required, free=free)
    # The boolean above is an integration assertion, not accepted standalone authority.
    raise Refusal('Production identity revalidation and authenticated maintenance integration not installed; full copy disabled')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('mode', choices=['prepare', 'prepare-fixture', 'copy-fixture'])
    parser.add_argument('root', type=Path)
    parser.add_argument('--identity-file', type=Path)
    parser.add_argument('--identity-sha256')
    args = parser.parse_args()
    if args.mode == 'prepare':
        if not args.identity_file or not args.identity_sha256:
            parser.error('prepare requires --identity-file and --identity-sha256')
        print(json.dumps(prepare(args.identity_file, args.identity_sha256, args.root)))
    elif args.mode == 'prepare-fixture':
        print(prepare_fixture(args.root / 'source', args.root / 'backup'))
    else:
        print(json.dumps(copy_fixture(args.root / 'backup')))
