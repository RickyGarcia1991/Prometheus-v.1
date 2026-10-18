# Surface lint and lightweight placement

This candidate adds an explicit remote-only placement option for bounded Python syntax checks and a pinned Ruff lint operation. The default syntax placement strategy remains unchanged. Remote-only jobs retain their exact approved destination while waiting and never fall back to the primary computer.

Lint requires a separately reviewed capability in the local worker configuration, with exact executable hash/version and fixed profile. Approval binds the selected source, device, account, worker identity and tool profile. Signing purposes distinguish lint from syntax. Durable claims prevent uncertain jobs from being rerun; recovery retrieves the original receipt.

Ruff runs with isolated configuration, no fixes and no cache. Submitted Python is analyzed as text without execution or import. Resource and output limits apply. Clean checks, findings and tool failures are reported separately. A passing lint check does not establish runtime correctness or qualify a model reviewer.

The native Surface development tests used generated clean, undefined-name, Unicode and CRLF fixtures, verified a nonexecution sentinel, and measured about 40 MiB peak child RSS in the primitive check. A full signed-entry test verified clean/findings receipts, retained receipts with executable permission removed, and rejection of a tampered tool before a new claim. These tests used isolated folders and ephemeral identities, not production enrollment.

The release retains dev30's existing advisory reviewer behavior. Optional specialist coordination, changed reviewer prompts and unsuccessful model experiments in the development tree are excluded from this candidate. Their evidence and plans remain preserved for further development.

Installation, production worker upgrade and live owner-authenticated browser acceptance remain separate steps. Preserve dev30 for rollback and verify the current data backup before activation. The package build and regression receipts are the authority for candidate integrity and exact test results.
