# Controller activation preparation: staged-signature correction

The cache transaction now revalidates the staged certificate/signature and signed version provenance before trusting its manifest or publishing files. A coherent modified payload plus rewritten unsigned manifest was accepted by the prior implementation; the retained regression records that failure. The corrected implementation rejects it on PowerShell 7 and Windows PowerShell 5 without publishing a cache or raising the protected floor.

Four signed-cache interruption/reload runs passed, and the combined source suite passed 16 tests. Independent focused review found no actionable defects. The installed R5 host remains unchanged; the new source requires a newly signed bundle and qualification before deployment.

The whole companion milestone is NOT complete. Remaining: authenticated release-pointer transaction with immutable package inputs and lifecycle serialization; updated signed host bundle and installed controller acceptance; coordinated physical SSD reconnect and Windows reboot. Original authorization, core dev55, user data and worker identities remain unchanged. Routine displays stay off. Ubuntu and the broader roadmap remain unfinished.
