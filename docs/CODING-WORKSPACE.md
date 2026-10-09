# Coding workspace

The interface can open an explicitly selected project, show source files, preview a complete replacement as a unified diff, apply that reviewed change, undo it and run a selected test preset. These are explicit UI actions. Model responses are text and never trigger a file write or test command by themselves.

## Integration contract

```python
from prometheus_assistant.coding_workspace import CodingWorkspace

workspace = CodingWorkspace(
    absolute_project_directory,
    absolute_edit_history_directory,
    resources=optional_portable_resources_directory,
    protected_roots=(signed_recovery_directory,),
)
```

The current signed application and resource directory are protected automatically. Pass any other signed recovery/release roots in `protected_roots`. The project directory must exist, be absolute, and be outside protected locations. The history directory should be under Prometheus-Data, outside user projects. One hash-named history subdirectory is used per project; constructing another workspace for the same project/history retains undo records.

Methods return JSON-compatible dictionaries or lists; invalid operations raise `ValueError` and filesystem failures may raise `OSError`.

| Method | Result / contract |
| --- | --- |
| `describe()` | `project`, `files`, `test_presets`, `max_file_bytes`, `test_execution` explanation |
| `list_files(limit=300)` | List of `{path, size}` for supported source/text files; bounded to 1,000 results and 3,000 visited directories |
| `read_file(path)` | `{path, content, sha256, size}`; retain the original SHA with the editor document |
| `preview_edit(path, content, expected_sha256)` | `{path, before_sha256, after_sha256, diff, changed}`; reads/checks the current file without writing |
| `apply_edit(path, content, expected_sha256)` | `{edit_id, path, sha256, changed}`; apply the exact reviewed content and original SHA; returns `edit_id=None` for a no-op |
| `undo_edit(edit_id)` | `{path, sha256, undone}`; restores the saved bytes only if current content still matches the applied edit |
| `test_presets()` | List of `{id, label, available, runtime}`; pytest also requires an installed pytest module |
| `run_tests(preset, target='', timeout=120, cancel_event=None, on_output=None)` | `{argv, exit_code, output, truncated, cancelled, timed_out, duration_seconds}` |

Run tests on the existing background job executor. `cancel_event` is a `threading.Event`; signal it from the cancel endpoint. `on_output(text)` receives bounded incremental text and must return quickly. Present exit code, cancellation and timeout separately: cancelled/timed-out work is not a passing test. Keep mutations and tests serialized in the UI. The module's lock serializes edits/undo across workspace instances in the same server.

The editor supports existing UTF-8 text files up to 256,000 bytes. A UTF-8 BOM and consistent Windows CRLF line endings are retained. Account for JSON escaping when choosing the HTTP request cap, or expose a smaller editor limit consistently. Render content, diffs and test output as text. Re-preview whenever the editor content changes, and disable Apply until the current content has a matching preview. Apply and undo check for conflicting newer file content, and backups are checksum-verified before restore. Backups are retained under the supplied data directory; the current implementation does not prune them automatically.

## Test presets

* `python-unittest`: `python -m unittest discover -s <absolute project target>`; directory defaults to `tests` when present, otherwise the project root.
* `python-pytest`: `python -m pytest <absolute project target> -q`; requires pytest already installed in the chosen Python runtime.
* `python-compile`: `python -m py_compile <absolute Python file>`; checks one source file without importing it.
* `node-test`: `node --test [absolute project target]`; an empty target uses Node's test discovery.

The portable Python/Node runtimes are preferred when present; otherwise the current Python and Node on PATH are used. No packages are installed automatically. Tests execute with the user's operating-system permissions and can run arbitrary code already present in the chosen project. The UI must call this out when the user selects Run tests; these presets are not a sandbox. Standard input is disabled, shell interpolation is not used, execution is limited to at most 600 seconds, and output is capped at 32,000 bytes while pipes continue draining. On Windows, an owned process job releases descendants on completion; cancellation/timeout also terminate the process tree. On other systems an owned process group is used.

Relative paths must remain inside the chosen project. Drive paths, parent traversal, alternate streams, symlinks, junctions/reparse points, credential files and private metadata directories are rejected. The workspace does not manage the signed Prometheus package, install dependencies, create new files, perform Git commits or push code. External coding tools remain available for larger project operations.

## Verification

Focused test file: `tests/test_coding_workspace.py`.

On the current Windows host: **22 passed, 2 skipped**. Fresh tests covered real edit/preview/undo byte preservation, persistent history, stale-edit and stale-undo conflicts, damaged backups, path/privacy checks, real Python unittest and compile jobs, a real Node.js test, output bounds, cancellation, timeout, and cleanup of a child left behind by a successful test. Creating symlink/junction fixtures was unavailable on this host; those two fixture tests are explicitly skipped, not counted as demonstrated operating-system boundary coverage.
