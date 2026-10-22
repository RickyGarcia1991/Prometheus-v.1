# Real Windows controller preflight

The corrected read-only preflight passed in the normal Windows profile through a temporary current-user task. Exact host and physical SSD identities matched, the SSD reported healthy, and ownership resolved to one controller plus the supervisor wrapper and child. Both temporary tasks were removed; the production task was untouched.

The first observation refused a historical drive-letter prefix in metadata. The corrected parser accepts only an exact release leaf or drive-root-prefixed leaf, ignores the old letter, and resolves against the independently verified SSD. All 26 parser/boundary tests and independent review passed. The original failure is preserved. Raw metadata was not modified.

R5 remains installed; R7 is a signed, unactivated candidate. Core dev55 and authorization are unchanged. This observation does not qualify activation: active-supervisor migration, lifecycle/eject locking, the production signed transaction adapter and installed acceptance remain. Physical reconnect and Windows reboot checks follow. Routine display updates remain off; Ubuntu remains pending.
