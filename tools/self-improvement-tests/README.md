# Offline regression runtime

The fixed host runner verifies and extracts these official PyPI wheels into a
fresh host-local temporary directory. It never installs packages globally and
never imports a candidate-supplied pytest, guard, bootstrap or test runner.
`LOCK.json` records upstream metadata URLs, archive URLs, sizes and SHA256 hashes.
Each unmodified wheel retains its upstream license and distribution metadata.

The complete baseline Python suite (`tests` and `tools/maps-studio`) and all
baseline UI `.cjs` scripts run against a temporary copy of candidate source.
Baseline tests and tools must be unchanged. The runner records real exits,
counts, named skips, log digests and before/after payload fingerprints. Missing
tests, timeouts, cancellation, altered files and isolation violations fail the
gate. No compile-only success can authorize a candidate.

Python audit hooks prevent ordinary accidental live-storage/network access.
They are not an OS security boundary against intentionally hostile native code.
The Node checks additionally use Node's permission model. Promotion and owner
authentication remain responsibilities of the trusted host coordinator.
