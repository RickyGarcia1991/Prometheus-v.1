# Runtime settings and Surface worker development

The separately approved dev25 release is installed. The changes in this document
are subsequent development work; they are not in the installed signed release.

## Runtime and measurements

The application prefers its packaged readiness helper, preserves healthy runtime
servers, distinguishes model runners from servers and reports bounded startup
phases. Maintenance offers an exact owner-reviewed Automatic/CPU preference for
future launches. It does not restart the current server. Preferences are local to
the current computer and protected by revision checks against stale reviews.

Two isolated native tests using the existing runtime and small model passed with
the explicit CPU library, including one using the actual saved preference's child
environment. Startup was about 0.58 seconds in each test. Defaults and visibility
overrides without the explicit CPU library stalled during GPU discovery. The
primary host launcher already selects CPU. This qualifies the CPU option; GPU
repair and repeated unattended startup reliability remain unfinished.

Project checks now capture job-associated committed memory, CPU and OS transfer
volume before closing the Windows job. These are kept distinct from direct-process
peak working-set history. Partial job snapshots remain partial, and transfer bytes
are not represented as physical disk pressure. Linux power observations read the
kernel's battery and AC information; the Surface reported external power and 100%
battery during native acceptance.

## Surface syntax worker foundation

The development worker has a fixed Python syntax-check operation for at most
eight portable relative paths and 512,000 total UTF-8 source bytes. It compiles
source in memory without executing it. Local permission, device/worker identity,
fresh observations, RAM/disk reserves, CPU and confirmed external power are checked
before a durable claim. Unknown facts defer work.

The SQLite ledger grants one claim per job. Reopening an unfinished claim returns
unconfirmed and does not execute again. Exact terminal receipts are immutable.
The ledger must be retained; deleting it is outside this guarantee and is not an
operation exposed by the module.

The later controller approval component now uses real ActionApprovals and SQLite
claims. Preparation displays exact source and destination without signing.
Denial is inert. Approval consumes the exact session-bound review, checks current
account/device/worker and policy, records a claim, then signs and dispatches once.
Its transport and signer are application callbacks; actual UI/transport wiring is
still required. Related approval, remote, runtime and project checks: 204 passed,
one Windows symlink privilege skip. This component was added after collection of
the full 2,608-test checkpoint and is covered by this separate run.

Incoming envelopes bind the exact content, destination, worker digest, purpose and
a validity period of at most five minutes. A separately provisioned local
certificate pin verifies RSA-PSS/SHA256 signatures. The existing primary-PC signer
was used through its normal Windows account protection for development acceptance;
no private key was exported and no live trust configuration was changed.

Native Surface acceptance passed original-request signature verification and
rejected altered content/signatures, wrong computer/worker, wrong certificate pin,
expired/future requests and a certificate outside its validity period. The actual
signed-job entry point then completed the bounded compiler operation.

The child enforces 256 MiB address space, five CPU seconds, an 8 KiB output-file
limit, no core dumps and a request wall deadline. Native qualification measured
about 25 MiB compiler peak working set. This measurement excludes monitor/browser
memory and is not a general workload forecast. A separate native test confirmed
source non-execution, failed syntax, durable result replay and abrupt-exit recovery.

## Validation

- Runtime/settings/helper focused checks: 74 passed, one symlink privilege skip.
- All 25 interface suites passed.
- Full runtime/job checkpoint: 2,445 passed, five documented skips.
- Remote worker, envelopes, certificate guards and Linux observations: 173 passed,
  two platform skips. Independent remote-only review: 157 passed, two skips.
- Actual Linux compiler and signature acceptance passed on the Surface.
- Fresh full integrated regression: 2,608 passed, seven documented skips; evidence
  is recorded in `Prometheus-next-integrated-full-tests.xml`.
- The preview restarted with isolated runtime preference storage. Live browser
  acceptance of this runtime preference still needs the owner's normal unlock.

Overlapping suite counts are not additive. Development Python does not include
the optional authentication dependency; its skip is distinct from the 12
authentication tests that passed in the installed dev25 package qualification.

## Required integration before automatic Surface work

1. Install a reviewed worker with persistent local enable/deny state and explicit
   trusted device identity, worker digest and sender pin; define revocation.
2. Wire the tested approval component to production signing, the exact selected
   project bytes and a verified connection.
3. Add bounded transport parsing, authenticated returned receipts and read-only
   recovery queries for uncertain dispatch outcomes.
4. Wire scheduler selection, dispatch and results into the approval screen and
   both progress displays. Preserve the worker's independent local veto.
5. Complete live browser acceptance, package verification and separate installation
   approval. No automatic worker service or job assignment is installed yet.

Broader plans still include independent model/provider review, GPU reliability,
physical disk pressure and battery-drain measurements, expanded operating modes,
memory/retrieval/vision/recovery improvements and qualified candidate integrations.

Native implementations follow [Python resource limits](https://docs.python.org/3/library/resource.html),
[subprocess timeout behavior](https://docs.python.org/3/library/subprocess.html),
and [cryptography RSA signature verification](https://cryptography.io/en/latest/hazmat/primitives/asymmetric/rsa/).
