# Prediction calibration and fair waiting turns

This milestone adds conservative forecast correction for supported local Python
checks and fair automatic attempts for approved waiting jobs. It does not qualify
a new reviewer, change worker identities, or change resource safety thresholds.

## Calibration

The original forecast is bound before execution to an opaque hash of the host,
runtime, implementation, project, check configuration, size bucket and policy.
Only trusted measurements associated with that reservation can complete it.
An unresolved reservation remains visible as an interrupted observation after a
restart. Failed, cancelled, unavailable or timed-out outcomes cannot be removed
to improve a calibration score.

For each measured metric, the first 20 complete observations freeze the training
membership. At least 10 subsequent complete observations validate a proposed
additive correction. Validation requires strictly lower mean absolute error,
improved underprediction, and bounded additional overprediction. Missing or
censored observations reject qualification. RAM and CPU corrections are capped
at 2 GiB and 60 CPU seconds respectively. A saturated cap is rejected.

Only qualifying positive corrections can increase an estimate. Existing cost
floors, RAM/disk reserves, CPU thresholds and unknown-sensor behavior remain in
force. Direct-process and job-associated CPU are compared by maximum, not summed.
Calibrated estimates never become training forecasts. Unsupported checks retain
the baseline; this version supports the native Python compile/unittest family.

Storage is bounded to eight scope identities and 100 observations per scope.
Full contexts retain their evidence and stop qualification; there is no rolling
reuse of validation data as training. Corrections are not guaranteed: a baseline
that already overestimates the workload should remain unchanged.

## Fair scheduling

An automatic tick considers due waiting jobs in ascending persisted retry-deadline
order, with approval time and job identity as deterministic tie-breakers. An
offline or busy oldest approval receives a later retry deadline, allowing overdue
peers their turn. Each tick still attempts at most one job. Explicit recovery and
explicit job requests keep their original behavior. Approval bindings, destination,
expiry, durable claims and prevention of duplicate execution are unchanged.

This is fairness between attempts, not preemption or a guaranteed completion time.
Destination outages and resource constraints can still defer execution.

## Qualification evidence

Synthetic tests establish eligible/rejected correction behavior and restart,
failure and fairness invariants. Separate generated native compilation runs on
each computer measure supported counters without importing the generated code.
Unsupported measurements remain unknown. These experiments do not tune production
history and do not establish general workload accuracy or reviewer quality.
