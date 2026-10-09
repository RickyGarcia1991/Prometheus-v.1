# SSD departure and arrival

Controller v0.5.9 and Host Agent v0.6.5 use one **Eject & verify** button. It records an owned pause on the internal disk, stops chat/Studio, checks SQLite, stops the known model runtime, closes drive-folder Explorer windows, cooperatively stops Desktop Commander and requests Windows safe removal. Green **SAFE TO REMOVE** requires a successful Windows request and an unmounted volume. If the request is blocked, the same button offers a retry and the log gives the reason. There is no separate prepare/eject button.

Windows vetoes and unknown drive-related programs remain blockers. No forced filesystem dismount or termination of unrelated applications is used. The process scan is an additional check, not a substitute for Windows' own handle checks. Stop alone never claims Windows has released the device.

The internal-disk supervisor keeps an owned eject pause until the exact USB identity is physically absent and its volume is absent. Windows PnP code 47 means prepared but still attached, so it retains the pause. Unknown device state holds the pause. There is no 90-second expiry. An explicit **Resume Prometheus** cancels an owned pause while the SSD remains mounted; it never removes another application's pause.

Scheduled worker tasks use the GUI-subsystem quiet host, which suppresses workers before creating PowerShell during eject. The supervisor and recorder stay on the internal disk; the recorder skips sampling while paused. The supervisor checks device metadata, not SSD files. On confirmed disconnection, Desktop Commander resumes. On the next insertion, one startup verifies the signed controller and core, checks the interface and saved memory, then opens the interface and requests a spoken greeting. Failed readiness gets no success greeting.

Previous task definitions and files are retained for recovery. Controller and Host Agent packages retain the existing pinned signing certificate. The portable memory database is never replaced by this upgrade. Physical unplug/replug, audio output and Windows veto behavior require checks on the actual computer; source tests alone do not establish them.
