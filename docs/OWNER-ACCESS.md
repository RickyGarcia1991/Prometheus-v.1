# Owner memory and local access

The current user-declared owner profile is read from durable memory on every text conversation turn, including a new session. Simple identity questions can be answered from that memory without starting a model. The model also receives bounded profile context for ordinary, coding and image conversations. A saved name is not authentication and does not authorize actions.

Owner access uses WebAuthn with Yubico's python-fido2 verification library. A successful passkey assertion requires the correct local origin, relying-party hash, current one-time challenge, signature, user presence and user verification. Registering a key alone does not unlock the workspace: the user must then verify its assertion. Interface sessions expire after 30 minutes and do not survive application restart.

First enrollment requires a random private setup ticket opened by the main workstation's owner setup launcher. The SSD stores only its SHA-256 hash. Once enrolled, that ticket cannot add another key; an unlocked owner session is required. The authenticator keeps private key material. Prometheus's data directory stores public passkey records, which are included in supported backups. Keep the owner-access folder with its memory when restoring. A previous backup may predate owner protection.

Use a current Edge or Chrome browser at `http://localhost:54555/`. A visit to the previous `127.0.0.1` address redirects to localhost when protection is required. Third-party sites and other origins cannot submit protected interface operations. Existing localhost scripts and automations no longer have unauthenticated history/job access once the gate is enabled; do not add an automation bypass.

Choose Windows Hello, a fingerprint, PIN, or compatible security key when offered by the browser. A device-bound Windows Hello passkey may not work on another computer. Register an additional portable authenticator or supported synced passkey before relying on a second computer. Enrollment, real-authenticator unlock, cross-device compatibility and recovery require live user validation; synthetic signature tests do not prove these.

This gate protects the HTTP interface. It does not encrypt the SSD, application memory, document originals or plaintext backups, and it does not protect against an OS account with filesystem access or a modified/offline application. It does not replace operating-system sign-in or drive encryption. It also does not add authentication to standalone CLI entry points. Protect those through Windows access controls.

Camera matching is not an unlock method. No raw face pictures or biometric templates are captured by this change. Windows Hello may use its own supported biometric hardware, with enrollment and templates managed by Windows. Ordinary-photo matching and anti-spoofing require separate enrollment, tested models and explicit local capture controls; they remain pending. Recognition from an image cannot establish that a live owner is present.

Dependencies are pinned and bundled for the portable Windows x64 Python 3.12 runtime, with their distribution licenses. See `OWNER-ACCESS-DEPENDENCIES.json` for provenance and original wheel SHA-256 values. No commercial account or external identity service is configured by this update.
