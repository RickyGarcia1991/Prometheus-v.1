# Response quality and interface maintenance

This update repairs the retrieval-to-answer path without replacing model weights
or adding cloud accounts. One inference runs at a time and existing model quality
exclusions, cancellation, memory-pressure limits and passkey checks remain active.

Document search considers the complete bounded question rather than its first
24 terms. Requests for exact component designators receive an explicit absence
result when the named designator is missing. Evidence packing selects verbatim
query-relevant windows, retains provenance separately and reserves space for a
second passage. The UI identifies truncation; excerpts are not full manuals.

Multi-part design/plan answers can use 640 or 896 output tokens on eligible
contexts, while short answers keep 384. The same allowance is reserved in the
prompt and passed to inference. Total and per-attempt times expose fallback costs.
Incomplete generations remain unsaved. These changes do not guarantee model
accuracy or faster completion for every prompt; measured acceptance is separate.

Assistant turns retain bounded, allowlisted source and model metadata in an
additive SQLite table. Existing text, trusted knowledge and version-2 readers
remain compatible. Saved conversations restore their exact retained excerpts and
labels; older conversations without metadata are not reconstructed or invented.

The owner access page derives controls from current authentication state and
offers explicit return and lock-to-test actions. Document sources use compact,
safe HTTP(S) links. Changing the selected code file invalidates its preview and
keeps the loaded draft isolated until the correct file is selected again.

Owner family fields support explicitly saved wife and son names, including
Unicode accents. Actual personal values belong only in the private memory data,
never source code, test fixtures or release packages. Owner facts personalize
conversation; they are not credentials and never grant access.

Release validation must include isolated regressions plus installed-app hardware
acceptance, saved citations, supported restart and user-completed authenticator
checks. Synthetic UI tests do not validate physical peripherals or remote devices.
