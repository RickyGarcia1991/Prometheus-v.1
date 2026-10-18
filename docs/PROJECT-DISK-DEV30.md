# Project-disk release — dev30

This candidate adds the last successfully opened project to Windows disk
observations. It builds on the signed dev29 payload; installed dev29 is the
installation and rollback baseline. Dev27 revision 2 is also retained.

Only successful validated Code opens select a root. Failed or denied opens leave
the prior selection unchanged. Changing projects clears displayed readings and
interval baselines while preserving the helper sampling interval. Concurrent
viewers share the observer; a physical device shared by data and project is
displayed once. The UI labels the selection as the last opened project.

Native Windows qualification established shared-device behavior and explicit
unavailable mappings. The Dell internal drive reports unsupported RAID; SSD
observations continue. No native identity checks were relaxed. These are
whole-device readings, not per-job costs or scheduler admission signals.
Surface workers, enrollment, approvals and history schemas are unchanged.

The prior source qualification passed 3,365 checks with 12 skips; supplemental
bundled-auth qualification passed 53 checks with one Windows symlink skip.
All 28 UI suites and the isolated native development browser check passed.
These are development results, not final packaged acceptance. Fresh package
results are saved externally against the immutable manifest.

Release acceptance requires signature/archive verification, complete packaged
regression and UI suites, portable-Python native project-disk checks, a packaged
dashboard browser check, read-only baseline preflight and isolated installer
recovery checks. Installation requires separate owner approval, a new verified
backup, retained rollback and post-start acceptance.
