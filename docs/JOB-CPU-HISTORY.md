# Separate job CPU history

Project resource reviews expose `job_cpu_prediction` alongside the existing
direct-process forecast. Successful runs expose `job_cpu_observation` in their
resource-history result. Only complete, finite Windows Job Object CPU counters
from every successfully completed planned check are eligible. Active members,
missing counters, mismatched checks, failures, cancellation and timeout do not
train this history.

The bounded `Maintenance/ResourceHistory/job-cpu.sqlite3` store contains CPU-only
samples keyed by host and check family. It uses the existing retained-maximum
margin (1.5 for fewer than five samples, then 1.2). Its separate retention cannot
evict or mix with the existing direct-process RAM/CPU samples. In dev34 the
prediction was observational and not used for admission. The CPU-aware admission
extension described in CPU-AWARE-ADMISSION.md can select an eligible forecast
after five actual CPU observations, solely to require more headroom for a heavy
check. Review data identifies whether job CPU was selected.

Job accounting covers associated processes only. Descendants created before job
assignment or allowed to break away may be absent. It does not establish complete
process-tree coverage. Peak committed memory is not resident working-set RAM and
is never substituted into the existing RAM forecast. Process I/O is not physical
disk I/O, and no battery cost is inferred. Linux remains explicitly unavailable
for native Windows Job Object counters; synthetic replay tests on Linux establish
history compatibility only.

There is no new background sampler, remote-worker capability, dispatch rule,
automatic promotion or approval bypass. A signed release installation requires
the owner's separate approval.
