# Windows enrollment checkpoint

Windows host authorization succeeded. The first disabled-task enrollment refused because Windows canonicalized SID strings to account names. The exact disabled task was verified and removed; signed file recovery awaits the requested Windows administrator prompt. No reconnect task has run.

Revision 3 fixes account normalization and preserves known legacy data. Its 174 integration tests and 11 signing-profile checks passed; the signed bundle verified. Source commit: 3fb0d4ef3b4bc56f1748a55e5f03952e8fd888a4. Original failure evidence is retained.

Pending: complete elevated r2 repair, enroll r3, verify disabled task, then controlled activation and reconnect/process-restart acceptance. Physical disconnect and Windows reboot have not been tested. Routine display updates remain off. No protected journals, private keys or authorization contents are included in this public evidence snapshot.
