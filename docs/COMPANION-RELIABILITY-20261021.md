# Companion source reliability checkpoint — 2026-10-21

Studio sends its existing rejection response before bounded unread-body cleanup. Cleanup consumes at most 64 KiB and uses a cumulative 200 ms deadline; it never parses or dispatches rejected content. That deadline does not bound header reading or response writing. Invalid/ambiguous framing is not drained. Existing authorization/status behavior is preserved.

The controller project now copies six shared lifecycle dependencies directly from HostAgent on build and publish. Missing canonical inputs fail the build. The read-only verify-package-inputs.py helper checks exact bytes and rejects missing/changed/extra scripts; run it explicitly after publishing. HostAgent ownership/mutex logic and controller UI were not changed.

Fresh integrated publication tests: 87 passed. Real .NET Release publish passed, and all six published script hashes matched canonical inputs. Studio UI suite passed. Independently, HTTP-focused checks passed 33 tests and packaging checks passed 12. A 600-request rejection probe observed only HTTP403 responses. Surface HTTP/lifecycle tests passed 18 with four Windows-only skips. Before-fix failures are retained locally and in the drive evidence checkpoint.

This is source/build qualification, not a new signed companion installation. Installed dev55 and existing companions remain unchanged. Remaining work includes controller arrival/departure UI reconciliation, quiet-host source/build provenance and full companion release/installed acceptance. Linux/Ubuntu qualification requires platform-appropriate runtimes, authentication, containment, audio and startup/drive behavior; Windows package success does not establish Linux readiness. No disk partitions were changed.
