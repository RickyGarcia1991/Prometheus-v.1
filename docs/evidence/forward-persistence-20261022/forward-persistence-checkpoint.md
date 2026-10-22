# Signed forward recovery: persisted-state qualification

Four isolated runs passed: initial interrupted publication and fresh-process reload on PowerShell 7.6.5 and Windows PowerShell 5.1.26100.9711. The real HostAgent state and package-selection functions were extracted without executing top-level code. The signed 0.5.12 recovery package was accepted at floor 0.5.11; an injected interruption after its verified backup left a recoverable 0.5.12 floor. The older 0.5.11 candidate was rejected afterward, including in a fresh process, and a lower floor write was refused without changing primary state.

No live authorization, state, tasks, pointers or controller were changed. These checks do not qualify controller activation, physical SSD reconnect or a Windows reboot. The initial Windows PowerShell test launcher inherited the newer host's module path; the preserved failure is resolved by removing inherited module-path keys case-insensitively and explicitly importing the matching host's utility module. No product correction was required.

Next: reviewed controller activation and forward-recovery transaction, including cache/pointer backup and fault recovery; then installed qualification and coordinated physical lifecycle checks. Broader roadmap and Ubuntu remain unfinished. Routine display updates remain off.
