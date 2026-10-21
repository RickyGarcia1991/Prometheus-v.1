# Selected-project disk observations — development after dev29

The Code workbench now selects an observation root only after a project opens
successfully, including its saved-history lookup. The validated root reaches
the primary interface's disk observer through ProjectResources. Failed or
denied opens and merely reading another file do not change the selection.

The selection is shared by viewers of the same app. The dashboard therefore
calls it the **last opened project disk**. It is held in memory, not restored
as an implicit project selection after restart. The configured data root stays
fixed; at most two roots are sampled, and a shared physical device is shown once.

Changing the selection clears displayed readings and interval baselines. It
preserves the last helper completion time, so rapid project changes cannot
launch a new helper more often than the existing ten-second interval. An
unchanged Windows path, including case and separator normalization, preserves
the cache. Sampling and selection share one observer lock outside app/scheduler
locks. Viewers receive isolated copies.

UNC and other unsupported roots are not sent to the native helper. The project
observation remains unavailable while app-data observations continue. A removed
local directory also remains unavailable. No thresholds, permissions, admission
decisions, worker protocol, workload history or source execution are changed.
The existing helper deadlines and native identity checks remain in place.

## Qualification

- Full development regression: 3,365 passed and 12 documented skips. A separate
  run using the installed release's bundled authentication libraries passed 53
  checks with one Windows symlink skip, covering all four missing-library skip
  categories from the full run. Overlapping totals are kept separate. All 28 UI
  suites passed.
- Ten focused selection tests cover switching, normalization, independent device
  rates, shared devices, missing/unsupported mappings, concurrent viewers, failed
  history reads, denied opens and non-Windows operation.
- Native Windows qualification opened a generated project through the real Code
  workbench dispatcher. Selection launched no helper. Four concurrent viewers
  shared one helper; 100 cached status calls took 5.920 ms in this run.
- Four native helpers peaked at 23.29–25.58 MiB. Selecting an SSD folder reset the
  interval and combined data/project roles into one observed device.
- The Dell internal disk reports a RAID bus. The existing native provider does
  not qualify that topology: its project row remained unavailable while the
  external SSD continued reporting. No topology checks were relaxed. Two fully
  measured separate devices are covered by deterministic tests, not established
  on this host.
- The development browser view uses the actual observer and dashboard renderer.
  It confirmed the live SSD readings and explicit unavailable project row.
  The installed application remains the separately qualified dev29 release.

Machine-local receipts and full-regression results are retained in the task's
outputs folder as `Prometheus-project-disk-*`. Packaging, signing, installation
review and installed-version acceptance for this new slice remain pending.
The dev29 release and its rollback copy are unchanged.
