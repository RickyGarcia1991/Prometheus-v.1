# Next bounded signed-controller integration proof

Wait for corrected core/fixture independent review before execution. No live adapter or installation.

1. Pin authoritative HostAgent source hash and exact signed .11 candidate/.12 recovery manifest, signature, certificate and payload hashes. Copy closures into one generated temporary fixture directory; verify readback. The parent supplies immutable expected identities, not journal contents.
2. Follow check-controller-cache-recovery.ps1 AST extraction to load only reviewed package/provenance and protected-floor functions. Supply temporary HostRoot, ControllerRoot, SecurityState and temporary SSD metadata; preserve trusted certificate constants. Never evaluate top-level HostAgent startup or task/process logic.
3. Use actual Get-Package and provenance checks for each source closure and a separate staged copy. A synthetic metadata pointer selects each package only inside its isolated verification context. This verification must not mutate the transaction's actual temp pointer. Do not call Sync-Controller: stage remains independently copied/verified and cannot advance floor.
4. First proof: real signed candidate and recovery pass; a modified staged manifest/signature/payload or provenance mismatch fails; staging leaves protected floor and pointer byte-identical. These replace fixture-only hash checks, not physical SSD identity checks.
5. Second bounded proof: bridge read_floor/set_floor to actual extracted Get-SecurityState/Set-SecurityState using only temp protected files. Test recovery slot committed before primary update, fresh subprocess restores highest floor, and lower version refuses. Preserve exact source hash and executed harness in evidence.
6. Then run one end-to-end candidate pointer commit plus explicit forward recovery using the authenticated transaction and per-target receipts. Failure injection remains isolated and must not modify extracted production function bodies without clearly labeling a test seam.

Remaining production gates: signed adapter self-identity, physical volume/profile validation, lifecycle/eject lock integration, controller/process ownership and owner unlock/UI acceptance. Passing this proof does not activate anything.
