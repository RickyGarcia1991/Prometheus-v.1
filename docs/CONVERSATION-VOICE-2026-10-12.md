# Conversation and voice update

Prometheus v0.8.0-dev14 keeps the outer page fixed. Replies live in a dedicated conversation box with its own scrollbar; the message box and voice controls remain visible below it. The Latest reply button returns to the beginning of the newest message. Reading earlier messages preserves your place when a new reply arrives.

## Voice controls

- **Text only → Record:** speak for as long as needed, then pause for about two seconds or press **Stop recording**. Whisper transcribes locally and leaves the words in the message box for review.
- **Spoken + text → Start voice chat:** explicitly begin a hands-free conversation. After a pause, Prometheus transcribes and sends the words as a chat question, speaks the answer, then listens again. The microphone is off during playback. **End voice chat** ends the loop and returns to Text only.
- The waveform shows measured microphone input while listening, and the local playback amplitude during a hands-free answer. A flat line can mean silence or an input-device problem; it does not claim speech recognition.
- **Stop recording** finishes the current recording and transcribes it. **Cancel recording / End voice chat** discards a recording still in progress.
- Leaving the tab, locking Prometheus, losing the connection, or closing the app stops hands-free listening. Returning to the tab does not automatically restart it.

There is no eight-second cutoff or thirty-second idle timeout. Prometheus waits for you to begin speaking until you end voice chat, leave the tab, lock the app or disconnect. While waiting, it retains only the latest 0.8 seconds of microphone audio in bounded memory; it does not fill the SSD with silence. That short pre-roll preserves the start of a sentence. About two seconds of silence after speech finishes a turn; short normal pauses continue recording. The existing local transcriber accepts an utterance up to 100 MiB, approximately 54 minutes of this PCM format; this is a resource boundary, not an advertised unlimited duration. Local transcription still has its existing five-minute processing deadline.

Ending a voice chat is a normal ready state, not an error. Leaving the tab shows an explanation and requires an explicit Start voice chat when you return. The microphone is off while Prometheus thinks and plays its answer. Wait for Listening before your next question.

Hands-free submissions use chat, never automatically execute a selected Smart home, code, remote or other tool action. A draft present before voice chat must be reviewed first. Editing while a recording runs holds the combined draft/transcript for review. Overlong transcripts are preserved for editing instead of silently truncated.

Speech detection uses measured amplitude with a short sustained-speech threshold. Background noise and very quiet speech can affect silence detection. This is a local turn-based voice conversation; interrupting playback by speaking is not implemented. Use End voice chat or Stop voice to stop the reply.

The update retains the existing portable Whisper, model and speech runtimes on the SSD; it does not download a cloud voice service or copy owner credentials into the release.
