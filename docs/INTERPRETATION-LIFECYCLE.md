# Revise or withdraw a historical interpretation

The interpretation lifecycle panel lists approved interpretations visible in the
latest 50 owner decisions. An owner can revise its explanation and selected native
observation, or withdraw it. Each change requires a new exact review in the current
owner session. This review records a historical interpretation; it does not run
work, repair files, clear a failed check or select an authoritative native result.

Revision binds the original interpretation, current lifecycle state, selected
current conflict snapshot and fact, new reason, conversation, owner and policy.
Native target, source and reference cannot change. A changed lifecycle or native
snapshot requires a new review. Withdrawal may retract a stale interpretation
without selecting a new fact. Withdrawal is terminal for that interpretation;
the existing interpretation panel can prepare a separate new interpretation.

## History and recovery

Original interpretation documents and native observations are unchanged. The new
EvidenceInterpretationLifecycle sidecar stores exact change attempts and their
completion records, while canonical decisions retain the compatible `other` kind.
An approved revision becomes effective only after its canonical decision and
durable completion both exist. Rejection and interrupted completion do not change
the effective interpretation. History distinguishes incomplete earlier attempts
from a later completed effective change.

SQLite serializes link and completion checks across processes. The reviewed state
digest binds the original completed interpretation and that interpretation's
ordered stored attempts. It excludes unrelated roots, current time and derived
native freshness. A newer linked attempt permanently prevents an older attempt
from completing, even if the newer attempt is denied. No pending review or grant
is restored after restart. Reads never dispatch work or create history.

Projections are bounded: at most 16 interpretations and 16 changes per item, with
the full counts and truncation visible. Opening an older change includes its exact
change alongside recent entries within that bound. Other approved interpretations
remain separate; differing effective facts in the same snapshot remain visible as
competing interpretations. Original contradictory approvals remain in history.

Rollback to dev52 can read original decisions and interpretations. Its older UI
does not understand the new revision or withdrawal history, so it provides a
legacy historical view, not the current lifecycle view. Preserve the sidecar with
user data so returning to the new release restores the lifecycle display.

Generated workflow tests establish these history and review behaviors. They do
not qualify a model, prove a real repair, or establish automatic remediation.
