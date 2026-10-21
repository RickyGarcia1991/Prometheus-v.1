# Owner interpretations of conflicting evidence

The separate **Review an evidence interpretation** panel reads conflicts from
the latest 50 owner decisions. Select one recorded observation and explain why
it should inform the investigation. Preparation creates a short-lived review in
the current authenticated session. The review shows the exact decision, native
fact, collected-input digest and reason. Only its explicit Approve or Deny
controls record that interpretation. Approval rechecks the complete bounded
native history before recording; changed inputs require a new review. A stale
proposal can still be denied without accepting its evidence.

An interpretation does not replace a native observation, hide a failed check,
select a winning result, replay a job or authorize a repair. Original conflicts
remain visible. Any resulting execution still needs its own existing review.

## Persistence and recovery

Preparation is RAM-only. A decision attempt first appends its exact proposal to
the owner-scoped EvidenceResolutions sidecar, then consumes the live approval
into canonical decision history, and finally appends completion. If completion
fails after consumption, history explicitly says completion is unconfirmed; the
approval cannot be replayed. Restart never restores pending authority.

Recorded interpretations retain the selected inputs. History derives a stale
label when the current collected inputs differ. This is historical consistency,
not an atomic snapshot or current device-health claim. Competing approved facts
from the same snapshot are flagged; neither wins by recency. Rejected or
incomplete interpretations do not become accepted interpretations.

Canonical receipts use the existing `other` kind so the previous release can
read them. The sidecar is independently validated, append-only, capped at 4,096
attempts and 32 MiB, and stores no browser binding or live grant. Each history
projection shows at most 16 items and discloses truncation. Existing stores,
native outcomes and Surface worker identities are preserved.

## Scope of validation

Generated test fixtures establish review, ownership, stale-input, failure and
history behavior. They do not establish model accuracy, real repair correctness,
or resolution of an actual production conflict. General conflict remediation,
cross-owner arbitration and remote execution are outside this release.
