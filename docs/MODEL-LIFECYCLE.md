# Model lifecycle and scheduling coverage

The interface establishes a fresh resource-mode scope inside its actual worker
thread. All Ollama streaming, preparation, chat and embedding requests share one
process-local model lease. A competing invocation defers immediately; it does not
start a second engine. The existing portable server retains its one-model,
one-parallel-request configuration. This does not lock unrelated programs or pool
RAM between computers.

Foreground user work remains distinct from optional enrichment. Balanced and
Performance use existing hardware/context and qualified-reviewer selection.
Performance does not automatically promote an untested model. Foreground work in
Idle or Battery Saver uses zero keep-alive; optional review and embeddings defer.
Critical Recovery or unavailable mode prevents new inference. Restrictive owner
intent applies immediately even during mode-display dwell.

Ordinary advisory review checks admission before inventory and before dispatch.
Specialist and measured reviewers retain their evidence gates. Failed native
checks remain failures if a reviewer cannot run. Optional semantic lookup falls
back to keyword results and indexing checks between batches. Due network research
is allowed in Idle, but Battery Saver/Critical Recovery defers background research,
source watches and update checks without consuming their due schedule.

Streaming checks cancellation and current mode during socket polling and before
accepting the final response. Blocking requests cannot interrupt an already
pending read: their bounded timeout remains, and a cancelled/disallowed response
is discarded on return. Interrupted inference attempts bounded serialized cleanup.
Cleanup reports released, still resident, or unconfirmed after checking runtime
residency. A failed cleanup is not a successful memory-release claim. No OS process
is terminated. Optional requests send zero keep-alive; ordinary foreground
residency remains hardware-aware and capped at fifteen minutes.

Leases release on every exception path. Backend exceptions create a bounded
30-second cooldown keyed by endpoint/model/context. Policy, cancellation,
contention and resource deferral do not create backend cooldown or model-quality
failure evidence. These transient cooldowns are not qualification evidence:
existing reviewer artifact checks and independently accepted configurations remain
required. Restart clears transient process ownership/cooldowns; persisted jobs and
owner approval remain governed by their existing recovery protocol.

Native qualification uses generated input and records model residency before load,
after load and after cleanup plus elapsed time. It is not a reviewer accuracy
benchmark and does not make an untested larger model eligible.
