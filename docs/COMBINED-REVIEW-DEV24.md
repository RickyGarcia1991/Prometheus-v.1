# Combined project review — 0.8.0.dev24 candidate

In Code, select a project and enter two to eight existing relative file paths,
one per line, under Combined change. Describe the requested change and choose
Show the exact change before applying. Review every diff and the listed checks.
Approval applies the selected files before running shared checks once. Denial
does not write project files.

A successful task saves a group undo receipt. Reopen the project after a browser
reload to recover the undo choice. Undo preflights every file and original backup
before restoring; a stale file or corrupt backup prevents starting the group.
An I/O failure during restoration reports how many files were restored, and
individual history records remain available for inspection. This is not a
filesystem transaction or interrupted-job recovery.

This release also accepts valid sparse-zero installed health reports while
rejecting failed, missing, duplicated or contradictory installation evidence.
Malformed group-history records cannot prevent opening the project.

Browser acceptance used the existing owner passkey and Qwen3 1.7B with two
disposable Python files. Denial, combined approval, two compile checks, page
reload/reopen, and exact-original grouped undo all passed.

Automatic coordinated file selection, unattended repair and crash-resume remain
separate roadmap work. This candidate requires separate installation approval.
