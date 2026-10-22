# Windows reconnect enrollment and restart checkpoint

R5 is enrolled and active in the normal Windows profile. The signed manifest is `b93d865e8560fad4ba2f5e7d91e5758400d2d864ae8f07fd0438c00f56ccd77e`; the fresh sealed transaction is `7b06d1cde83a4b8ebb5a8ac1fa086887`.

181 related runtime/enrollment tests passed. The replacement helper passed 21 isolated tests, including duplicate-module cleanup. Independent source, upgrade and restart-helper reviews passed. Native process tests are included in the related suite. The unchanged strict signing profile retains its earlier 11 passing checks.

Actual Task Scheduler duplicate-start, stop and restart checks passed. Exactly one launcher/supervisor pair was present; stopping removed both; restarting created a fresh pair. The existing application listeners and real-profile controller survived both stop and restart, and authorization hashes matched. All five installed payloads match the signed package. Existing memory is present. Core dev55 and the Surface worker were not replaced.

Earlier failures are retained: MSIX profile redirection, initial orphan supervisor, active-controller refusal, and the two-module activation-helper error. The completed enrollment was not repeated after that activation error: activation succeeded in a fresh normal-profile process. The helper correction was tested separately. The original executed helper is preserved. A prior command to update the temporary execution task used the wrong PowerShell parameter set; it retried the old create-only entry and made no successful new enrollment. The corrected task action was verified before the R5 run.

Arrival now opens the ordinary owner-unlock page instead of treating its authentication requirement as startup failure. The protected interface still needs the user's passkey; no authentication bypass or protected-status acceptance is claimed.

Physical SSD disconnect/reconnect and a full Windows reboot were NOT performed. The forward controller release and broader roadmap remain separate pending milestones; Ubuntu is not started. Routine display updates remain OFF.

## Recovery and rollback

The original authorization and sealed journals remain in the real profile. The bounded upgrade saved both original signed payloads and the exact old disabled task XML before removal. R3 and R5 signed closures are included on the drives. Recovery must validate the relevant signatures, exact task owner and file hashes first; do not automatically re-enable the old R3 supervisor, which has the recorded child-lifetime defect. No recursive profile replacement or user-data restoration was performed. The temporary inspection/execution tasks will be removed after the final readback; the enrolled logon supervisor remains enabled.

Tested source commit: `cffb080f63ea98b795d9f6600fb0174a8cb33c81`.
