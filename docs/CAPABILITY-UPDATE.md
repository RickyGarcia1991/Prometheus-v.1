# Main-workstation release and portable SSD updates

The development release is `Prometheus-v0.8.0-dev3-main-workstation-20261010`
(`0.8.0.dev3`). It includes the capability improvements described in
`CAPABILITIES-0.8.0.dev2.md` and a two-signer trust transition.

The original identity is `898F702114B0F889763589C4057DC19CF1657CD5`.
The main-PC identity is `1E0F99439DD072E014D81C5574B325C97EA4AC98`.
Both are explicitly pinned. Possession of an arbitrary certificate is insufficient.
The original key remains on its original Windows account; the main-PC private key
is DPAPI-encrypted on the main PC, outside the repository and portable SSD.

## Build and validate

Use `tools/build-capability-update.py --baseline <original-dev1-directory>
--output <new-output-directory>` with the project Python environment. The builder
is deliberately specific to this reviewed release. It checks the original and new
verifier hashes separately, excludes private keys, user data and model weights,
and creates a deterministic unsigned bundle. It does not sign, activate or run
behavioral tests. Keep the unsigned artifact and validation report.

`tools/Sign-Capability-Update.ps1 -Mode Validate -Bundle <bundle>
-ExpectedBundleSha256 <reviewed-bundle-hash>` independently checks the bundle.
Its `Sign` mode is retained only for the original certificate-store signer.

For subsequent releases on the main PC, use the host-local signing tools under
`Workstation/Prometheus/Signing`. Review the source, version and verifier pin;
build into a new directory; validate every manifest-listed payload; sign the exact
reviewed manifest; then independently run the pinned verifier. Never edit signed
payloads in place. No GitHub credential is needed for local signing.

## One-time trust transition

Before the SSD can accept the main-PC key, the original signer must authenticate
a bounded transition inventory containing exact destination paths, original and
replacement hashes, both certificate identities, and the new release manifest.
The original pinned verifier authenticates this transition before any new guard
is executed. The main-PC key can then sign the new core and Host Agent packages.

Activation is a separate stopped-session operation. Verify both candidate and
recovery copies, close the interface, hold its existing memory-session lock,
create a verified host-local backup, and save exact original trust files. Replace
only the signed inventory destinations, with the bootstrap and release-selection
metadata last. Journal each operation and restore the original inventory if a
write or validation fails. Do not overwrite user memory during code rollback.
Keep previous signed releases and recovery packages unchanged.

The new recovery edition is `Prometheus-Recovery/Main-Workstation-20261010`.
Paths in portable configuration remain relative to the detected SSD root. Host
drivers, credentials, development environments and private keys stay on the PC.
The active selection and activation receipt determine what is actually running;
building this candidate alone does not activate it.

## Scope and acceptance

Model weights have not been trained. Architecture, source-grounded retrieval,
model routing, image input and capability UI improvements are application changes.
Acceptance includes old/new signer compatibility, forged/tampered input rejection,
memory preservation, repair from the matching recovery edition, normal launcher
startup and live feature checks. Physical eject/reconnect is a separate check.
GitHub publishing uses this PC's own authenticated account; never copy credentials
or machine authorization files from another PC.
