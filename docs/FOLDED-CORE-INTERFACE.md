# Folded core interface — 0.8.0.dev7

The home uses the owner's selected Preview 2, Folded core. The same green, cyan and violet material flows inward while idle, thinking and speaking. Thinking adds moving code behind the sphere. Speaking brightness and deformation follow the actual local PCM output; silence removes the response pulse while the underlying swirl continues. This animation does not indicate autonomous learning or background jobs.

The approved artwork is sampled only for the orb and atmosphere. The header, messages, input and buttons are functional HTML. Capability suggestions are removed. The Owner button reveals the existing workspace, history and helper settings. The existing server-side passkey gate still protects the interface and APIs when enrolled. This does not create a second authentication boundary or a guest mode. A reload starts a fresh visible conversation rather than restoring saved messages automatically.

Text only is the default on each page load. Spoken + text is explicit opt-in and reads completed replies submitted in this tab. Read answer remains available on demand. Stop voice, a new request, owner lock, lost authentication, page hide and page close stop browser playback. There is no online voice fallback. Answer text remains visible if local speech is unavailable.

The protected `/api/speech` endpoint uses the existing `voice.speak` application policy. It accepts only answer text, up to 4,000 characters, and renders the installed Windows voice into a temporary mono 16 kHz PCM file. It never opens a microphone or plays audio on the server. Preparation is serialized, checks owner access and shutdown, has a 45-second deadline and a five-minute/9.6 MB output limit, and removes temporary files after delivery. The browser decodes that PCM and derives its pulse from the same playback clock and waveform.

Display quality still adapts to measured headroom. Hidden tabs stop animation and speech. Reduced motion and Still use the selected static artwork. WebGL failure also falls back to that artwork.

This release changes the visual and speech-output interface. The existing eight-second, review-before-send dictation path is retained. Start/stop extended recording and a microphone waveform are separate unfinished work; the orb's speech pulse is not a microphone meter. Physical microphone pickup and perceived speaker timing require a person at Dell and are not established by muted browser tests.

Release checks include Python owner/HTTP/voice policy tests, all UI lifecycle suites, JavaScript syntax checks, and an isolated Chrome run using synthetic conversation data and installed Windows speech. Personal conversation data, owner credentials and host authority state are excluded from the source and release artifacts.
