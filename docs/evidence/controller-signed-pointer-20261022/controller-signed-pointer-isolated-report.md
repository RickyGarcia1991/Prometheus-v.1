# Real signed controller isolated proof

Both configured pwsh and Windows PowerShell 5 passed verify/interruption/fresh-resume: six subprocesses. HostAgent source is pinned to 26f47da1f529f7d7b2b469cbc30b4376ee8b82be3f624bf2a3247e69bfc896a5. Exact candidate/recovery manifest pins and all copied payload hashes are recorded in the runner/results.

The harness extracts only package/provenance and protected-floor functions, never top-level HostAgent logic or Sync-Controller. Real signed .5.11/.5.12 sources and separate staged copies verify. Manifest, signature and provenance tampering refuse. Staging/verification leave the floor unchanged.

The isolated Test-ProtectedFile wrapper terminates the process immediately after actual Set-SecurityState verifies the flushed .12 recovery slot, before primary update. The runner checks primary still .10 and recovery slot .12. A new process recovers .12 with actual Get-SecurityState, verifies .12 package and rejects .11 downgrade. The wrapper is an explicitly labeled test seam; product functions/source are unmodified.

The PS helper refuses roots outside the generated normalized system-temp prefix and reparse ancestors. No live profile, controller/task/process launch or drive writes occurred. The initial Windows PowerShell5 file invocation hit existing execution policy; its failure log is preserved. Tests were then invoked as in-memory script commands without changing execution policy.

This proves isolated real signed closure checks and protected-floor recovery only. It does not bind the Python transaction to a production adapter, prove physical SSD identity/lifecycle locks or qualify live activation.

Final PS helper SHA256: 801be248679f2aa7b94690410da9f745b359a4e5f8c2dfdc8126ca70194732d9
Runner SHA256: 098b941083c1f9a7b896c275148a96cae24c63419e81abee6556982420ea0d00
Evidence: controller-signed-pointer-isolated-results.json
Retained initial failure: controller-signed-pointer-failure.log
