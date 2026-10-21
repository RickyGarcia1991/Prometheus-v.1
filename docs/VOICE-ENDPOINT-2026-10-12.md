# Prometheus voice update — dev16

This update improves how Prometheus decides that you have finished speaking. A local speech detector separates speech from background sound, and a short confirmation period helps prevent isolated clicks from keeping a turn open. The microphone waveform still shows sound levels; movement in the waveform does not necessarily mean that speech was detected.

## Have a hands-free conversation

1. Open Prometheus and unlock it with your usual passkey.
2. Select **Spoken + text**, then **Start voice chat**. Keep the Prometheus tab visible.
3. When **Listening** appears, ask your question naturally.
4. At the end of your question, pause for about **1.6 seconds**. Prometheus should finish the recording automatically. You do not need to press **Stop recording** for each question.
5. Allow time for local transcription and answer generation. The pause finishes your recording; it does not mean the reply will start immediately. Watch the status as Prometheus processes your words and speaks its answer.
6. Wait for **Listening** to return before asking your next question. Press **End voice chat** when you are finished.

Short pauses can stay within the same turn. A pause longer than the ending interval can finish a question even if you intended to continue. Prometheus detects a speech pause; it does not predict the meaning of an unfinished sentence.

## Recording and playback controls

- **Stop recording** finishes the current recording for transcription. It remains available if background sound prevents automatic ending or you want to finish immediately.
- **End voice chat** ends the hands-free conversation. A recording still in progress is discarded.
- **Text only → Record** lets you dictate without automatically sending. After you pause or press **Stop recording**, review the transcript in **Speak your mind** and press **Send** yourself.
- The microphone is off while Prometheus processes your question and plays the answer. Speaking over a reply does not interrupt playback; use the visible voice controls to stop it.
- Leaving the tab, locking Prometheus, losing its connection or closing the app ends voice chat. Returning requires **Start voice chat** again.

There is no eight-second recording cutoff or thirty-second idle timeout. Prometheus can wait for you to begin speaking. While waiting, it keeps only the latest **0.8 seconds** of sound in memory to preserve the start of your words, rather than filling the SSD with silence. The existing **100 MiB limit per recorded utterance** and local transcription processing limits remain. These are resource limits, not a new recording timer.

If you finish a recording before speaking, Prometheus should return to its normal ready state without sending a question. A draft already in the message box remains available. Hands-free voice sends a chat question; it does not automatically carry out a selected smart-home, code or remote action.

## What runs locally

Speech detection uses **Silero v6.2.0** with the existing **whisper.cpp b5454 CPU runtime**. The required detector model and runtime files are included with the portable release on the SSD. Transcription and speech playback remain local. This update adds no cloud audio service, ONNX runtime or Python package dependency, and does not put owner credentials into the release.

The detector examines 32-millisecond audio frames, requires three speech votes within 160 milliseconds, and ends a turn after about 1.6 seconds without confirmed speech. Background sound, microphone placement and very quiet speech can still affect the result. This is a local conversation that alternates listening and speaking; it does not provide playback interruption by speaking or exact ChatGPT voice behavior.

## Confirm automatic ending

The earlier dev15 test required manual **Stop recording**. Dev16's physical microphone and speaker confirmation is **still pending**; software checks cannot confirm how your microphone and speakers sound in your room.

After the updated release is installed:

1. Start a voice chat and ask **“What is six times seven?”**
2. Pause without pressing Stop. Confirm that recording ends, the transcript is correct and you hear **42**.
3. When **Listening** returns, ask **“Now add eight to that.”** Pause again and confirm that you hear **50**.
4. Press **End voice chat** and confirm that listening stops normally.

If a turn does not end automatically, **Stop recording** remains available. Whether it needed that button is part of the test result, not an automatic-ending pass.
