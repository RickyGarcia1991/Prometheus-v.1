# Studio shutdown and eject boundaries

Studio refuses startup when the existing host's eject-mode.json or active-chat.shutdown marker is present. A running Studio stops when either appears. The launcher refuses before runtime access, logging or dialogs, so refusal does not write back to an ejecting SSD. Markers remain owned by the host controller and are never removed here. Missing LOCALAPPDATA and unrelated marker paths do not falsely stop portable sessions.

This restores a source gap relative to the historical Studio companion while preserving the newer Host Agent ownership and mutex protections. It does not install a companion or exercise physical removal.

Verification: six genuine pre-fix failures demonstrated missing boundaries. Revision 2 passed 55 Python tests and four subtests, including ten lifecycle cases; 13 existing Studio UI tests passed. Surface passed six native cases with four explicit Windows-launcher skips. Root review additionally caught and corrected refusal writing a startup log.

A broader publication check passed 62 tests and four subtests but hit WinError 10053 in the existing origin-rejection test. A bounded probe reproduced the same issue on the unchanged baseline (198 HTTP 403 responses and two connection aborts); the changed tree returned 200 HTTP 403 responses. HTTP handler ASTs are identical. The intermittent Windows response-delivery issue remains documented rather than claimed fixed; no rejected request was accepted.

No production worker, identity, service, security setting or installed release changed. Physical reinsertion and audible greeting are not covered by these tests.
