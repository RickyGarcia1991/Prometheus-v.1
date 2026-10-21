# Prometheus dev21

Start voice chat now prepares the selected local reply model, local speech
synthesis and speech recognition while the microphone is off. The interface
starts listening after preparation succeeds. End voice chat,
owner lock, a hidden tab or a lost connection cancels preparation and prevents a
late result from opening the microphone. Preparation does not add a synthetic
message to your conversation. A short fixed synthetic phrase exercises the local
speech tools without playing audio; its temporary audio and decoded text are
discarded. The first preparation can still take time.

Active voice jobs refresh more frequently. Voice timing details distinguish
capture setup, the detected silent pause and local transcription. These numeric
measurements do not record an extra copy of your audio. The current pipeline
still uses local acoustic turn detection, Whisper transcription, a local reply
model and Windows speech synthesis; it is not the ChatGPT voice implementation.
Preparation reduces first-use work after you speak, but does not make the models
permanently resident or eliminate processing time. Memory pressure and other
programs can still affect latency.

## Full semantic memory

Open Owner → Documents, code & connected computers → Integrations and select
**AgentMemory semantic service**. It uses the full local AgentMemory runtime,
iii engine and MiniLM embeddings to remember, recall and forget project notes.
Semantic recall can find a related idea even when the query uses different words.
The existing **AgentMemory classic** tool remains available for lightweight
keyword notes. The two tools keep separate project stores and approvals.

Examples to type or speak:

- `Remember with AgentMemory semantic in project workshop: The reference drawings are in the blue folder.`
- `Recall with AgentMemory semantic from project workshop: Where are the plans kept?`
- `Give AgentMemory semantic full access to project workshop until I revoke it`

Prometheus explains each proposed action's reason, effects and resources. You can
approve the exact pending proposal by text, voice or button. Continuing task
access uses the duration you select, including **Until revoked**. Select an
existing approved task with **Use in this conversation**. Approving the classic
tool does not approve the semantic service. A policy update requires fresh review.

The semantic engine starts for an approved operation and shuts down afterward.
It does not run as a permanent background service. A write is verified by opening
the staged state again before the active project generation is replaced. Failure
or cancellation before publication preserves the previous active generation.
Cancellation cannot undo an operation already published.

Runtime files are under the SSD's
`Prometheus-Resources/Integrations/AgentMemoryService/0.9.30`; portable notes are
under `Prometheus-Data/Interface/Data/Integrations/AgentMemoryService`. Supported
backups include both classic and semantic project stores. The upstream viewer is
disabled. Internal engine sockets are temporary loopback listeners without a
separate bearer token; this is not OS isolation from other programs running as
the same user. There are no automatic conversation-capture hooks or hidden owner
logs.

## Other runtimes and portability

The Browser Use public-page reader remains available with owner approval. The
full model-driven Agent was tested separately: the current local model did not
reliably select observed links or report requested facts. Those failures do not
qualify autonomous browsing, and the full Agent is not enabled by this edition.

OpenViking 0.5.0 and its native Windows imports were verified in a host candidate.
Its complete memory workflow remains unqualified within the measured memory
budget, so no OpenViking service is installed and no existing notes are migrated.
Other resource catalogue entries retain their individual reference/setup status.

These new native bundles target Windows x64. SSD paths are resolved relative to
the portable resource root. Task authority, passkeys and signing credentials stay
on their issuing host. Operation of these Windows bundles on Ubuntu has not been
demonstrated; moving the SSD alone does not make Windows executables Linux-native.

The signed release receipt and separate installed runtime reports provide the
actual verification results. Microphone comfort, audible response timing and
speaker output still require a physical check by the owner after installation.
