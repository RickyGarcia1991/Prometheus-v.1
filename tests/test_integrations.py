from prometheus_assistant.integrations import available_local_workers, launch_integrations


HELP = """Launch the Ollama interactive menu.
Supported integrations:
  claude          Claude Code
  opencode        OpenCode
  codex           Codex
  cline           Cline
  qwen            Qwen Code

Examples:
  ollama launch
"""


def test_launch_integrations_parses_supported_tools(monkeypatch):
    class Result:
        returncode=0
        stdout=HELP
    monkeypatch.setattr("subprocess.run",lambda *a,**k: Result())
    assert launch_integrations("ollama.exe")=={"claude","opencode","codex","cline","qwen"}


def test_failed_launch_probe_is_empty(monkeypatch):
    class Result:
        returncode=1
        stdout=""
    monkeypatch.setattr("subprocess.run",lambda *a,**k: Result())
    assert launch_integrations("ollama.exe")==set()


def test_available_workers_intersects_host_and_ollama(monkeypatch):
    monkeypatch.setattr("prometheus_assistant.integrations.launch_integrations",lambda executable=None: {"opencode","qwen"})
    monkeypatch.setattr("prometheus_assistant.integrations.coding_agents",lambda: {"opencode":"C:/opencode.exe","claude":"C:/claude.exe"})
    assert available_local_workers()=={"opencode":"C:/opencode.exe"}
