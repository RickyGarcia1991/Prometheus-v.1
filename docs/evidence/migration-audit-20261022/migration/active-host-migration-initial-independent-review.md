# Active HostAgent migration core review

The reviewed development core's phase ordering persists intent before each operation and replays pending phases. Quiescent locks cover backup/replacement and are released before restarting. Completed replay invokes only verification/assertion. No native provider exists; these observations do not qualify live migration.

The 33-case provider suite covers only an originally enabled and running task. Before freezing the stated broader contract, add disabled/stopped and enabled/stopped baseline fault/replay cases proving exact enabled-state restoration and zero start. Also mutate observed provider authorization/task/file/owned-process state during recovery and require refusal before mutations; changing the supplied plan alone does not exercise those native-provider obligations. Requested from implementation owner.

## Enrollment compatibility

Keep the completed R5 enrollment journal as immutable historical evidence. ReconnectEnrollment recovery requires its exact package identity and refuses completed-enrollment repair; changing its identity to R7 would invalidate its provenance. Existing activator and upgrade helpers expect exact original closure and cannot automatically accept the changed HostAgent.

A separate authenticated migration receipt can be sufficient for new migration-aware consumers only when they validate the original R5 enrollment, the exact signed R5→R7 single-file delta, unchanged task identity/configuration and authorization, and current installed hashes. The receipt must have a durable completion rule and idempotent interrupted publication recovery. It does not make the old R5 enrollment receipt an R7 receipt. Do not invoke the old activator against R7 or weaken its package check.

No production state was read or changed for this review. Native task/process ownership, physical-root resolution, session-scoped exclusion and durable receipt adapter remain gates.
