# SSD development update — October 7, 2026

The latest published stable tag remains v0.3.0. The Python control-center package metadata remains 0.3.0; the application work is on feature/ai-orchestration. Drive Controller is independently versioned 0.5.2-dev. The previous SSD controller was 0.5.1-dev at a363d37; the source launcher used the older 191fcc5 package. This update exports the current Git commit and records it in PROMETHEUS-SSD-STATUS.json rather than assuming every component has the same version.

Changes:
- Honor explicit portable Python and resource-root settings. Quote runtime paths containing spaces and return a failure for missing configured runtimes.
- Read bridge stdout and stderr concurrently to prevent pipe deadlock.
- Detect the actual SSD letter for status/start/stop instead of always using D.
- Copy the controller to a verified host cache before launching it, preventing the dashboard itself from retaining the SSD executable.
- Include the PowerShell engine and watcher in Git, and retain prior source/controller releases and launcher backups.

Validation: 121 Python tests pass on Windows; all five launcher tests pass, including a runtime path with spaces and missing-runtime rejection. Controller Release publish passes. Deployment, engine and watcher scripts pass the Windows PowerShell parser.

All three ZIM archives are present, totaling 65,752,720,977 bytes. The existing catalog records SHA-256 verification; a new independent full-file checksum audit is running during deployment. ZIM reader/search integration remains pending: file installation is not yet offline article retrieval. Archive files, models, private memory and recovery data are not committed to GitHub.

Green controller status means ready to attempt Windows Safely Remove; successful physical eject is a separate Windows operation. This update does not claim reboot/reinsertion endurance or a successful physical eject.
