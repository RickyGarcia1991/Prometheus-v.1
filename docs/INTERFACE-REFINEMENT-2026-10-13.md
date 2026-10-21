# Prometheus dev19 interface refinement

The main view now displays a lightweight waveform. Select **Conversation** to open the compact transcript, which scrolls inside its own panel. Select **Waveform** to return. Sending a message or receiving a reply preserves that choice. The text composer and recording/stop controls stay available in both views.

The waveform uses three Canvas 2D ribbons with measured microphone or playback amplitude. It does not load WebGL shaders or texture images, and it stops its continuous drawing when idle, hidden, or replaced by the conversation/workbench. Active drawing is bounded to 20 frames per second and 960 by 260 backing pixels. The retired textures are removed from this edition. Previous signed editions remain available for rollback.

The Owner button uses a vector person icon. The lower right footer reads **New Prometheus version 0.8.0.dev19** and **Created by Ricky Garcia**. The displayed version comes from the running application.

## Voice and responsiveness

In **Spoken + text**, choose **Start voice chat**. Pause after speaking to finish a turn. Prometheus transcribes, answers, speaks, then listens again after its final audio segment. **Stop recording** completes a turn manually; **End voice chat** ends the session and stops the microphone. Hiding the page also ends voice chat. There is no eight-second recording timer.

Speech synthesis now prepares a short opening segment first and can prepare one following segment during playback. Cancellation invalidates pending segments, and the microphone cannot restart between segments. The speech-server lock is released before returning a completed audio response so the next segment can start without a false busy error.

This reduces the preparation work before initial playback; it does not remove model loading or generation time. It is not a copy of ChatGPT's internal voice implementation, semantic turn prediction, or playback interruption by speech. The existing locally measured speech endpoint remains unchanged.

Intent selection now keeps translation of technical prose in translation and recognizes common non-programming uses of words such as rust, Java, python, function and script. Explicit coding requests and programming-language conversion still select coding.

## Verified resource catalogue

Select **Public reach → Agent resources · offline catalogue**. Search a name or capability, or leave the field blank to browse up to ten entries. The result labels each project's publisher, capabilities, requirements, verification status and Prometheus integration status. This operation works without Internet access and does not execute or install a listed project. Source links allow independent review.

Agent-Reach's existing public-source adaptation, Firecrawl search and MarkItDown document conversion remain available. Browser Use, AgentMemory and OpenViking need separate executable adapters and runtime qualification before their full capabilities can be called installed. Bolt.new is an external development workspace. Scientific, diagram, cybersecurity and harness collections are references with different runtime and licensing requirements. Ambiguous names are labelled likely matches. The community cybersecurity collection is not an Anthropic-published repository. BreachDirectory is unverified, and leaked credentials are not an integrated credential source.

## Code changes and repair

The existing owner-approved self-improvement workflow can propose and test a bounded change, explain it, and request exact approval by typed text or transcribed speech before signed host activation. It does not silently grant itself permission for arbitrary rewrites. The current adapter targets one existing implementation file within its documented size and path limits. Signing tools and host credentials remain on the PC.

The new **tools/prepare-core-restoration.py** prepares a separate host candidate from a matching signed recovery when input copies contain missing, changed or uniquely identifiable misplaced files. It binds approval to the exact plan, current file bytes and pinned tools, verifies the signed recovery and final candidate, and preserves both input copies. Unknown extras, ambiguous placement and changed plans require review. It neither detects faults continuously nor activates a repair. This edition therefore supports verified repair preparation, not unrestricted autonomous filesystem repair.

The private owner-only hidden activity logger was canceled and is not included. Normal diagnostics and approval receipts retain their existing behavior.

## Portability and qualification limits

Application code, bundled resources, portable runtimes, models and user data retain the SSD layout. Runtime paths follow the SSD label or project root; host tools, signing credentials, caches and temporary tests remain on the PC. A physical Ubuntu run still needs independent qualification, particularly for Windows-native voice and the platform-specific Python bundle.

Passing software tests does not guarantee every model answer is correct. Separate model comparisons and manual factual checks accompany this release. Unavailable optional providers or credentials are reported as setup requirements rather than relabelled healthy. Complete release manifests, signatures, recovery copies and the active selection are verified during installation.
