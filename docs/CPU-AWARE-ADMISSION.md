# CPU-aware project admission

Project checks with at least five actual CPU observations in either direct-process
or associated-Windows-job history supply an optional learned CPU-work estimate.
The estimate is the maximum eligible retained forecast plus its existing margin,
never the sum of direct and job counters, which may overlap. Missing CPU values
do not count toward the five-sample requirement. Each history remains host/check
family scoped and bounded. Cold or sparse histories retain the previous policy.

The initial uncalibrated policy defers a learned workload of at least 30 CPU
seconds when current measured CPU load is at least 60 percent. The independent
85 percent hard ceiling, missing-signal guard, RAM, disk, battery and active-lease
guards still apply. This adds a stricter gate; it never relaxes an existing guard.
CPU seconds are accumulated execution time, not wall time or CPU percentage.
Partial job descendant coverage cannot establish idle headroom or relax policy.

Reviews disclose cpu_admission with selected source, actual CPU sample count,
conservative estimate, eligibility and uncalibrated policy. Reservation refreshes
signals and passes that same learned estimate into lease acquisition. A busy
review cannot become approved by dropping the estimate during acquisition.
Observation only trains future estimates; it never changes the current lease.
No background sampler, worker change, automatic retry or owner-approval bypass
is introduced. Existing unrelated Scheduler callers omit the optional estimate
and retain their prior behavior.
