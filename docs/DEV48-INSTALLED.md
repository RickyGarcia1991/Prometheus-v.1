# Dev48 revision 2 installed acceptance

Release `Prometheus-v0.8.0-dev48-capability-revisions-20261018-r2` is installed and owner-authenticated interface
acceptance passed. A fresh 184-file backup and isolated restore
verified before activation. Three signed release copies verified. Dev47 remains
available for rollback; the production Surface worker and identities are unchanged.

Manifest SHA256: `7a79e079dc0142ba4103081e4d8a949c3ad9b4238258e415d8a678b731315706`

Archive SHA256: `e336995413c6e90d6243a1447356df4f119e3dd1c35310cf77246cd0d2746e2d`

Startup, retained production job claims/results, six scheduling-mode cases,
native Windows measurements, decision/revision history, 18 device-health cases,
and seven capability-revision/shared-evidence groups passed. Installed Request
changes controls, owner history refresh and the decisions dashboard passed the
live browser check. Prometheus reconnected to the Surface using its existing
account. Both Desktop Commander devices subsequently reported online.

The installed owner decision panel is empty; the 12 generated revision decisions
were exercised in a separate preview. Historical native failed-check evidence
remains visible. No production source edit or worker job was dispatched during
installed acceptance. The broader roadmap is still unfinished.

Qualification: 4,356 packaged tests passed, 14 documented skips, one exclusion;
33 UI suites, 40 isolated Surface tests, 70 installer tests and 20 acceptance-helper
tests passed. The initial fixture-only failure remains preserved. The application
bytes used by live preview and Surface checks match revision 2 exactly; the final
full regression ran on revision 2. See DEV48-VALIDATION.md for qualification scope.

This branch and its draft PR are published for review, not merged.
