# Dev52 revision 3 — corrected package in final validation

Installed dev51 remains active, with the production Surface worker unchanged.
The earlier agent-capacity error cleared on retry. Independent source review
is now complete and found two UI defects, both corrected: an incomplete review
could enable decision controls, and valid long Unicode reasons were hidden in
saved history. The original candidates, failing probes and earlier passing
release evidence remain preserved.

The final thirteen-file source is independently reviewed and frozen. Revision 3
is signed with 1,020 payload files. All 34 packaged interface suites, 91 focused
UI checks, and 32 independent backend/UI/history probes passed. Portable preview
startup and native fixture checks passed. All 132 installer, 10 native-helper,
and 21 independent installer probes passed; final installer and release-gate
review are being completed.

The full revision 3 Windows regression is running. The new authenticated browser
check needs the normal owner passkey unlock at http://localhost:54605/ after the
restart that loaded the corrections. Earlier full regression and live recovery
passed on revision 1; those are retained as earlier evidence, not relabeled as
new revision 3 runs.

The 206 passing Surface native checks are reused only after verifying all 22
input files are byte-identical in revision 3. No new Surface run or production
worker change is claimed. The unchanged backend retains original failures and
contradictions; an owner interpretation does not authorize execution or retries.

The repository's draft review and both drives already contain revision 1
preparation evidence (145 matching files plus a nine-file status supplement).
Revision 3 source and final evidence will be checkpointed separately. Once the
release gates pass, standing owner permission permits the fresh stopped-data
backup, verified isolated restore, activation with dev51 rollback and installed
acceptance. Normal device authentication after activation still needs the user.

Five broader workstreams and optional Ubuntu remain unfinished.

Manifest SHA256: `23a2ba224ebbfb760e101838e72c05ab64cbb5735438c7d182b37a3d1166d20c`

Archive SHA256: `09538a8dc613a4cd49dc10b35cb64675ef336b44d7c2a76ace468ddbde8fb720`
