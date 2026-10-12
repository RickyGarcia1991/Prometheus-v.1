# 0.8.0.dev5 — permissions and boundaries

The workbench gains a searchable Boundaries tab and read-only CLI/HTTP inventory.
Core decisions use the existing ToolSpecs and evaluator. Browser, owner, advisory
and external limits remain separately labelled. No grants, settings writes or
executor calls are introduced; an allowed preview never authorizes execution.

Independent review F1 is fixed by refusing non-object preview arguments,
including JSON null, without altering existing runtime argument normalization.
F2 is fixed by broadcasting shared API authentication loss before navigation;
Boundaries clears observations and input immediately and discards pending replies.
The expired document stays disabled even if an earlier status response arrives.

The release retains the signed dev4 Hugging Face and security hardware catalogs,
Windows owner checks, runtime dependencies, memory behavior and existing policies.
The original dirty checkout and its index are preserved separately. This release
branch selects verified dev4 project source and retains repository-only files.
Two legacy host-specific backup scripts stay in the verified baseline payload and
are not newly published; generic source helpers are under tools/Prometheus.RemoteBackup.

Regression assertions cover null arguments without executor/grant effects,
shared and own-endpoint 401 handling, pending preview and inventory races, lock,
disconnect and close. Release validation receipts distinguish synthetic fixture
results from normal owner authentication, installed health and physical checks.
The full common-policy/scoped-grant architecture and local-model hardware-answer
qualification remain incomplete. No model is promoted by this feature.

Source alone does not prove signing or activation. BUILD-PROVENANCE.json in the
signed payload binds the build to its exact Git commit. External release receipts
record SSD activation and recovery-media read-back results.

The integrated Windows regression passed 1,166 Python tests with 3 retained skips,
9 UI scripts and 7 JavaScript syntax checks. Isolated Chrome rendering passed at
1440, 1024 and 390 pixel widths, including shared-auth-loss stale-response handling.
The skips cover an absent offline signer-migration helper and unavailable Windows
symlink privileges. These results do not certify real-owner or model acceptance.
