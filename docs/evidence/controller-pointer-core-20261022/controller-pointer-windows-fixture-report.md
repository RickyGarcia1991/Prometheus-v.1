# Isolated Windows provider qualification result

23 tests passed in 7.60 seconds using the configured Windows Python environment. All operations target pytest-generated system temporary directories. No live profile, drive, task, controller, application or protected production state was read or modified by this fixture.

The fixture uses actual Windows CurrentUser DPAPI for a random HMAC key, a no-share CreateFile lock, flushed writes and atomic same-directory replacements. Thirteen abrupt os._exit(77) boundaries are followed by fresh Python subprocess resume and repeated idempotent completion. Tests cover cache staging without floor/pointer mutation, recovery-first durable floor publication, pointer/receipt publication, a second process denied by the lock, modified intent/key/source/pointer/floor and wrong root nonce. Candidate and forward-recovery receipts are immutable separate files keyed by the independently qualified manifest, preserving both outcomes under one transaction.

Core source remains unchanged. Fixture SHA-256: 956287ab90826646b76bc00dc6dc9380c674257af5ae92e84f8d92b6f9cebefb
Tests SHA-256: a5d9f000aa2fe9dc77715a341c362c5f3f8ee283b3820b6e30e3ed9d5b5eda77
Log: controller-pointer-windows-fixture-tests.log

## Qualification limits

This is an isolated test adapter, not production qualification. Package closure is modeled with generated hash fixtures, not real certificates/signatures. Floor files model durable recovery ordering rather than production authenticated floor custody. Fresh subprocesses use the same Windows account; cross-account DPAPI denial is not exercised. Abrupt process exit proves application-crash recovery, not power-loss/filesystem-controller durability. The temp-root nonce/fixture plan is a harness boundary, not a production authorization mechanism. Windows PowerShell 5 compatibility is not established by Python tests.

Physical SSD/profile identity, lifecycle/eject lock interoperability, pause/process ownership, protected floor integration, immutable signed source/cache verification and signed adapter identity still need production integration. No launch or activation is performed or claimed.

