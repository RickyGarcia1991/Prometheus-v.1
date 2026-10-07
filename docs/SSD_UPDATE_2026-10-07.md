# SSD development update — October 7, 2026

The latest published stable tag remains v0.3.0. The Python control-center package metadata remains 0.3.0; the application work is on feature/ai-orchestration. Drive Controller is independently versioned 0.5.2-dev. The previous SSD controller was 0.5.1-dev at a363d37; the source launcher used the older 191fcc5 package. This update exports the current Git commit and records it in PROMETHEUS-SSD-STATUS.json rather than assuming every component has the same version.

Changes:
- Honor explicit portable Python and resource-root settings. Quote runtime paths containing spaces and return a failure for missing configured runtimes.
- Read bridge stdout and stderr concurrently to prevent pipe deadlock.
- Detect the actual SSD letter for status/start/stop instead of always using D.
- Copy the controller to a verified host cache before launching it, preventing the dashboard itself from retaining the SSD executable.
- Include the PowerShell engine and watcher in Git, and retain prior source/controller releases and launcher backups.

Validation: 121 Python tests pass on Windows; all five launcher tests pass, including a runtime path with spaces and missing-runtime rejection. Controller Release publish passes. Deployment, engine and watcher scripts pass the Windows PowerShell parser.

All three ZIM archives are present, totaling 65,752,720,977 bytes. A fresh independent full-file SHA-256 audit passed all three archives at 08:18:30 Eastern, reading all 65,752,720,977 bytes in 570.5 seconds with 8 MiB buffers. ZIM reader/search integration remains pending: file installation is not yet offline article retrieval. Archive files, models, private memory and recovery data are not committed to GitHub.

Green controller status means ready to attempt Windows Safely Remove; successful physical eject is a separate Windows operation. This update does not claim reboot/reinsertion endurance or a successful physical eject.

Additional corrections found during actual smoke testing:
- The dashboard refreshes every five seconds while idle, with an overlap guard during startup/shutdown. It no longer starts with an unmeasured 100% progress bar.
- The engine recognizes a fresh archive-verification report paired with a live audit worker and keeps eject readiness blocked during the audit. This behavior was observed at 96.73% of the real audit.
- Cold Ollama initialization exceeded the old bootstrap's fixed wait. The new startup helper polls actual local API readiness for up to 120 seconds, records server logs, and reuses an existing SSD server process.
- Vulkan is disabled by default in the SSD bootstrap for the current laptop; an explicit OLLAMA_VULKAN environment setting can override it. CPU-mode initialization was observed completing, followed by HTTP 200, successful Prometheus doctor, and an actual local model response. This does not establish Vulkan as the sole cause of the earlier delay.

Actual lifecycle smoke test passed at 08:22:51 Eastern: chat reached its input prompt using a temporary SQLite database, acknowledged the controller shutdown request with exit 0, and the engine verified the normal databases and released the SSD model server. The archive monitor now displays the measured status file and exits on completion instead of continuing an obsolete approximate download percentage.
