# Read-only permissions and boundaries

This isolated architecture phase adds a **Boundaries** tab to the workbench,
`GET /api/permissions`, `POST /api/permissions/preview`, and
`python prometheus.py permissions`. It does not change effective grants or move
existing dispatch paths to a new policy engine. No settings-write or grant API
is added.

The inventory is generated from the running source's built-in Core ToolSpecs,
the existing `evaluate_tool_request` function, the evidence registry, registered
browser modes, the actual browser capture response-header constant and the
current HTTP owner's in-memory session state. Source SHA-256 fingerprints form
the policy revision. Version 1 is the response schema; the revision identifies
the reviewed policy/source implementation, not a newly signed release.

Each row gives an outcome, scope, reason, source, enforcement point, observation
basis, current grant/expiry observation, adjustment availability and external
requirements. The four categories separate application permissions,
authentication/integrity, advisory behavior and external limits. Source-review
rows and conditional browser rows are explicitly distinguished from evaluated
Core outcomes. `not_configured`, `unavailable`, `not_observed`, `conditional`
and `deny` have different meanings.

An `allow` outcome is limited to the named policy layer. Inventory does not
validate arbitrary file paths, check model readiness, authenticate standalone
CLI use, encrypt data or establish device availability. Core inventory and
preview use default policy with no plan approvals or custom guardrails. The
existing HTTP owner gate is optional when no owner setup exists; the view says
`not_configured` rather than implying a verified owner in that case. HTTP
inventory and preview otherwise require the same current owner session, local
request token, Host/origin and fetch-site checks as existing protected routes.

The Core preview takes exactly `worker`, `tool` and `arguments`. It looks up a
registered built-in tool, validates its existing argument schema, derives risk
flags from its ToolSpec and calls the existing evaluator. Unknown capabilities,
malformed arguments (including explicit JSON null) and caller-supplied risk/approval fields are refused. The arguments value must be a JSON object. It
never calls an executor, creates a plan, returns a grant, stores arguments or
echoes their content. Every response says `authorizes_execution: false`.
Target-specific validation still happens at the actual execution boundary.

The CLI command runs before memory/activity setup and reports owner state as
unobserved. Neither surface instantiates maintenance workers or reads saved
watch schedules, update preferences, smart-home credentials, remote pending
actions, owner public-key records or personal memory. Merely loading this view
does not start a model, contact a provider or probe hardware. The browser renders
metadata as text, clears observations on lock/disconnect/close and immediately on
any shared API authentication loss, rejects late responses after those transitions, and supports searching, categories and normal
workbench keyboard navigation. Snapshot time and partial coverage are visible.

## Scope and remaining work

All currently registered Core tools, evidence tools and browser modes are
enumerated. Enumeration is not proof that every effect has common policy
enforcement. CLI/headless commands, HTTP dispatch, background work, provider
adapters and host lifecycle/signing/recovery helpers still need complete mapping.
The 23 built-in Core tools include the dev4 read-only Hugging Face and security
hardware catalogs; those catalogs do not enable downloads or device actions.

This phase deliberately does not migrate approvals, introduce expiring task
grants, change path/DNS validators, relax capture rules, modify owner setup,
activate updates or promote a model. Future integration must preserve browser
and CLI distinctions and keep rejected-action tests at each execution boundary.

## Validation and acceptance limits

`tests/test_permissions_inventory.py` exercises live registry/evaluator output,
default/custom deny precedence, malformed and unknown previews, side-effect
traps, owner-state observations, real loopback HTTP handling with synthetic
sessions, lock/expiry/token/origin checks and CLI memory non-creation.
`tests/ui/test-permissions.cjs` exercises discovery, filtering, literal rendering,
preview requests and busy/error/lock/disconnect/stale-response transitions in a
minimal DOM fixture. Existing owner cryptography and runtime tests remain in
the full regression suite; no real owner passkey is used by these fixtures.

The isolated worker's accompanying evidence records exact runs and line
observations. Line coverage is not branch or exhaustive security coverage.
Synthetic Chrome rendering and the failed-navigation authentication race passed
on Dell at desktop, laptop and narrow widths. This does not establish Windows
Hello, local-model hardware reasoning, physical device or cross-host SSD acceptance.
Release status is recorded separately in RELEASE-PROVENANCE.json and the release
receipt. A source checkout alone does not establish signing, activation or live acceptance.
The integrated test-permissions-auth.cjs regression covers authentication loss
from other API calls, both inventory endpoints and pending inventory/preview replies.
