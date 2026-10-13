# Portable refinement and owner-approved application changes

The conversation panel uses about half of the space above the composer. Long
replies scroll inside that panel, and the composer stays visible. The voice
monitor shows the current input or playback signal with separate state and
bounded history. Spoken + text continues listening after each completed reply
until End voice chat. The existing tested speech endpoint remains in place.

Python 3.12.15 and Ollama 0.40.3 were qualified with the Windows portable
installation. Both pypdf import paths use 6.20.1. The superseded PDF source and
wheel are removed from this release. Previous signed releases and runtime trees
remain in recovery locations, outside the active application.

The local model connection retains an idle model for fifteen minutes when a
host with at least 12 GiB RAM has at least 2 GiB available, two minutes with
1–2 GiB available, and thirty seconds on smaller or more constrained hosts.
This reduces repeated cold loads between questions; it does not keep a model
resident indefinitely or remove the generation memory checks.

Explicit JSON requests enter structured generation. When source-only output is
explicitly requested without Markdown, one complete code fence is removed while
preserving the source. This is a presentation correction, not a claim that
model reasoning or generated code is always correct.

## Preparing an application change

In Maintenance, describe one concrete change and select **Prepare tested
change**. An optional existing source path narrows the target. Chat also accepts
an explicit request such as **Improve Prometheus: handle empty input**.

Prometheus selects a fitting installed local helper, proposes a replacement in
an isolated copy, and runs the complete Python and interface regression groups.
There are at most three generation/repair attempts. A failed candidate is
removed from its staging job and cannot be approved. Test results remain as
ordinary development evidence.

A qualifying proposal explains the action, reason, effects and tests, and
shows its exact diff. Review it, then say or type **yes, approve**, **go ahead**
or **reject**, or use the corresponding control. Approval applies to that exact
proposal in the same unlocked owner session and conversation. It expires after
ten minutes and can be used once. A model reply cannot act as your approval.

After approval, the configured Windows host rechecks the candidate and reruns
the fixed tests. It builds and signs a separate release, verifies recovery
copies, closes the old app cooperatively, selects the replacement and checks
startup. Activation status reports completion, failure or recovery needs;
queuing a change is not reported as a successful installation. Personal memory
is backed up and is not replaced by generated source.

## Current boundaries

- One existing Python, JavaScript, CSS or HTML application file per proposal.
- Complete files are bounded to 1,500 bytes by the current local-model adapter;
  the exact request must fit the selected helper's context without truncation.
- Tests, dependency pins, signing, approval and policy code cannot be changed
  by this proposal path. Larger or multi-file work uses the normal reviewed
  development workflow.
- Regression checks use separate host test directories and process guards.
  They run with the host user's permissions and are not an OS sandbox for
  deliberately hostile code.
- Signing tools and the signing identity are configured per Windows host.
  Their credentials are not copied to the portable SSD. SSD tool paths follow
  the volume label instead of assuming a drive letter.
- This release provides Windows runtimes. Running the same SSD on Ubuntu needs
  separately qualified Linux runtimes and voice support.
- The permissions inventory lists actual policy behavior. An allowed policy
  outcome is not a health check, physical-device test or approval for an action.

Provider-dependent search, social accounts and transcript sources still depend
on their actual service, credentials and supported formats. Unsupported services
are reported accurately rather than marked healthy without a working check.
