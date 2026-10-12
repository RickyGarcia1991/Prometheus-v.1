# Dell release-signing certificate: independent trust verification

**Historical Ubuntu checklist; superseded by the Dell signing and activation receipts.**

The Dell identity uses the existing host/account-bound DPAPI signing helper, not
a mandatory Windows certificate-store enrollment. The independent trusted
certificate SHA-256 below matches the recorded Dell and recovery-media identity.
The encrypted owner backup was recovery-verified on October 11. Do not rotate
keys or repeat password setup because this historical checklist says pending.
Current release operations must use the reviewed signing helper with exact
manifest/verifier pins and consult the latest release receipt.

The original checklist follows for provenance; its pending and store assumptions
do not describe the current Dell workflow.

The portable SSD release `Prometheus-v0.8.0-dev3-timing-ui-20261011` is internally consistent:
610 source manifest files match, and the RSA/SHA-256 release signature verifies against its embedded `MANIFEST-PUBLIC.cer`.

However, that self-signed certificate is on the **same SSD** as the signed release. It is not an independent trust anchor. To establish signer identity, compare with the Dell's trusted Windows certificate store **without using the SSD's copy of the certificate**.

## Read-only Windows PowerShell procedure

When Dell development is safely paused, open its normal PowerShell session and run:

```powershell
$certificates = @(
    Get-ChildItem Cert:\CurrentUser\My
    Get-ChildItem Cert:\LocalMachine\My
) | Where-Object { $_.Subject -like '*Prometheus Main Workstation Release Signing*' }

if (-not $certificates) {
    Write-Host 'No matching signing certificate in the Windows certificate stores.'
}

foreach ($cert in $certificates) {
    $hasher = [System.Security.Cryptography.SHA256]::Create()
    try {
        $digest = $hasher.ComputeHash($cert.RawData)
        $fingerprint = ($digest | ForEach-Object { $_.ToString('x2') }) -join ''
    } finally {
        $hasher.Dispose()
    }
    [pscustomobject]@{
        Subject = $cert.Subject
        SHA256 = $fingerprint.ToUpperInvariant()
        NotBefore = $cert.NotBefore
        NotAfter = $cert.NotAfter
        CertificateStore = $cert.PSParentPath
    }
}
```

**Expected SSD-embedded certificate SHA-256 fingerprint:**

```text
F8526536AB8526C57C68F4CC5B65CC1ABA814B99734EE24AD2FD15BB3450967C
```

This is **SHA-256 of the certificate's DER bytes**, not the familiar Windows certificate SHA-1 Thumbprint. Compare the exact 64 hexadecimal characters. No private keys, passwords or signing credentials need to be exported.

If the expected fingerprint is absent or differs, **stop**. Do not execute the quarantined release, copy SSD memory, merge code, re-sign artifacts, or assume the unsigned trust chain is valid. Resolve the Dell's signing history and owner authorization first.

If it matches a certificate independently established on the Dell, record the check's time, Windows store, and intended release. The read-only Ubuntu verification helper can then be rerun with the trusted SHA-256 fingerprint:

```bash
~/prometheus-env/bin/python ~/Prometheus-Ubuntu/tools/ubuntu/ssd_release_signature.py --mount /run/media/ricky-garcia/Prometheus-2TB --trusted-cert-sha256 F8526536AB8526C57C68F4CC5B65CC1ABA814B99734EE24AD2FD15BB3450967C
```

This only verifies signer consistency and file integrity. It does **not** automatically establish that an arbitrary signing certificate was legitimately enrolled, that the code is safe to run, or that Windows applications and drivers will work on Linux. Those require additional review and test evidence.

Dell is primary; Ubuntu is portable secondary. Do not use the SSD as the destination for Ubuntu development code until the separate integration plan is approved and successfully tested.
