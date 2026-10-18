# Prometheus resource admission development checkpoint

Updated 2026-10-15T16:22:54.062145+00:00.

Installed dev26 revision 1 completed its post-restart browser recovery acceptance. The original Surface result reopened without a new claim or execution. Both approved job records remain unchanged, the denied job is absent, and the source execution marker is absent. This is completed-job recovery after a full server restart; it does not claim a forced mid-job production crash test.

## New development work

The next unfinished resource-manager item now has an authenticated, on-demand Surface resource query connected to Prometheus and exact approval. It reports available RAM, CPU use, free disk capacity, battery percentage, external power, and local worker enablement. Signed responses bind the current request nonce, exact device and worker version, and expire within 30 seconds. Queries carry no source and create no job claims.

Prometheus shows the signed reading and expiry in the worker panel and a snapshot in the exact source/destination review. It checks fresh readings again after approval and before claiming a new job. Low resources, battery operation, stale or untrusted readings, changed connection or policy, and Stop/Lock cancellation leave the job unclaimed. The UI explains a deferred result and requires a new review. A local worker admission check still precedes execution.

Windows resource signing and verification now use explicit resource-specific operations, distinct from job signing. Slow resource queries release the owner and connection locks, then revalidate owner, cancellation, policy, identity and expiry. Existing completed-job recovery remains query-only. Independent review findings were fixed and the final review reported no unresolved actionable defects in this slice.

## Fresh verification

- Final combined development regression: **2,846 passed, 7 documented skips**, no failures or errors, 325.11 seconds.
- All **27 interface test suites passed** after the final source changes.
- The 89 focused approval, cancellation, freshness, disconnect and Windows crypto checks passed before the complete run; overlapping counts are not additive.
- Real Windows signing/verification tests passed with ephemeral test identities; production keys were not changed.
- Native Surface component test passed using a temporary candidate bundle and temporary identities. Enabled and disabled responses authenticated correctly, no jobs database was created, and production enrollment was unchanged. Each measured request took about 0.26 seconds; the sampled Surface had about 4.3 GiB available RAM and external power. These are two observations, not a general performance benchmark.
- Browser component check passed for fresh readings, timed expiry, exact-review snapshot, and deferred result text. It used the real development markup and script with explicitly simulated readings/API results; it is not a production end-to-end acceptance claim.
- The seven skips are recorded individually in the JSON evidence: five Windows symlink privilege cases, one offline migration preparer absent from the app payload, and one Linux-native worker acceptance case covered separately on the Surface in the worker qualification workflow.

## Installation and remaining work

The installed app and enrolled production worker remain dev26. This development checkpoint is not packaged or installed. A reviewed worker upgrade is needed for the new resource-query protocol; an older worker cannot provide a valid new advertisement and is handled as unavailable for new admission. Packaging, full application-to-Surface preview acceptance, and separately approved installation are still required.

The automatic destination chooser and durable waiting queue for approved work remain unfinished. This change supplies authenticated resource facts and the claim-time admission prerequisite. The next implementation should choose among allowed destinations using workload estimates and fresh observations, preserve exact source/destination approval, expose why work waits, and never replay uncertain claims after restart. Other roadmap work remains independent provider review, GPU reliability, physical disk pressure and battery-drain measurement, expanded modes, and memory/retrieval/vision/recovery integrations.

## Evidence

- Prometheus-dev27-resource-admission-validation.json: counts, skip reasons, scope and SHA256 of the 19 changed implementation/test files.
- Prometheus-dev27-final-regression.log and .xml; Prometheus-dev27-final-ui-tests.json.
- Prometheus-dev27-native-resource-query.json.
- Prometheus-dev27-resource-ready.txt, -expired.txt, -review.txt, -deferred.txt and -deferred.png.
- Prometheus-dev26-r1-installed-acceptance.json and -installed-recovery.jpg.

Both monitor services receive this checkpoint. Their timestamps describe the most recent recorded milestone; they are not evidence that development continues between chat turns. Browser memory is additional to the lightweight monitor servers, and Surface RAM is not pooled with Dell RAM.
