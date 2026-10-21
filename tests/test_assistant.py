from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import threading

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def ollama_stub():
    state = {
        "models": [{"name": "llama3.2:1b", "model": "llama3.2:1b"}],
        "requests": [],
        "mode": "valid",
    }

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def reply(self, payload, status=200):
            body = json.dumps(payload).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def do_GET(self):
            if self.path == "/api/tags":
                self.reply({"models": state["models"]})
            else:
                self.reply({"error": "unexpected path"}, 404)

        def do_POST(self):
            request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            state["requests"].append(request)
            mode = state["mode"]
            if mode == "http_error":
                self.reply({"error": "fixture failure"}, 500)
            elif mode == "redirect":
                self.send_response(302)
                self.send_header("Location", state["url"] + "/unexpected")
                self.end_headers()
            elif mode == "bad_json":
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"not json")
            elif mode == "array":
                self.reply([])
            elif mode == "wrong_role":
                self.reply({"message": {"role": "user", "content": "invalid"}, "done": True})
            elif mode == "malformed":
                self.reply({"message": {"content": 17}, "done": True})
            elif mode == "incomplete":
                self.reply({"message": {"content": "unfinished"}, "done": False})
            elif mode == "oversized":
                self.reply({"message": {"content": "x" * 3_000_000}, "done": True})
            else:
                self.reply({
                    "model": request["model"],
                    "message": {
                        "role": "assistant",
                        "content": "Local reply: " + request["messages"][-1]["content"],
                    },
                    "done": True,
                    "eval_count": 9,
                    "total_duration": 1_000_000,
                })

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    state["url"] = f"http://127.0.0.1:{server.server_port}"
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        yield state
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=3)


def run_cli(db, *args, stub=None, extra_env=None, input_text=None):
    command = [sys.executable, "-X", "utf8", str(ROOT / "prometheus.py"),
               "--memory", str(db), "--no-agent", "--model", "llama3.2:1b"]
    # Protocol fixtures do not load weights; their behavior must not depend on
    # the test host's free memory. Auto-selection is covered separately.
    if stub is not None:
        command.extend(["--base-url", stub["url"]])
    command.extend(args)
    env = os.environ.copy()
    if extra_env:
        env.update(extra_env)
    return subprocess.run(command, text=True, encoding="utf-8",
                          input=input_text, capture_output=True,
                          timeout=15, env=env, cwd=ROOT)


def successful_json(result):
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def ask(db, stub, prompt="test message", session=None):
    args = ["ask", prompt, "--json"]
    if session:
        args.extend(["--session", session])
    return successful_json(run_cli(db, *args, stub=stub))


def test_source_launcher_exposes_chat_commands(tmp_path):
    result = run_cli(tmp_path / "memory.sqlite3", "--help")
    assert result.returncode == 0, "The runnable assistant entrypoint is missing: " + result.stderr
    assert all(name in result.stdout for name in ["chat", "ask", "doctor", "sessions", "history"])


def test_ask_returns_a_local_answer(tmp_path, ollama_stub):
    result = run_cli(tmp_path / "memory.sqlite3", "--no-agent", "ask", "test message", "--json", stub=ollama_stub)
    answer = successful_json(result)
    assert answer["reply"] == "Local reply: test message"


def test_agent_mode_is_default_and_plain_chat_is_explicit_opt_out(tmp_path, ollama_stub):
    default = subprocess.run(
        [sys.executable, "-X", "utf8", str(ROOT / "prometheus.py"), "--memory",
         str(tmp_path / "agent.sqlite3"), "--base-url", ollama_stub["url"], "--model", "llama3.2:1b", "chat"],
        text=True, encoding="utf-8", input="/exit\n", capture_output=True, timeout=15, cwd=ROOT)
    assert default.returncode == 0
    assert "Core: agent + safe local tools" in default.stdout
    plain = run_cli(tmp_path / "plain.sqlite3", "chat", stub=ollama_stub, input_text="/exit\n")
    assert plain.returncode == 0
    assert "Core: plain local-model compatibility mode" in plain.stdout
    help_result = run_cli(tmp_path / "memory.sqlite3", "--help")
    assert "--no-agent" in help_result.stdout


def test_successful_exchange_is_saved_atomically_with_unicode(tmp_path, ollama_stub):
    db = tmp_path / "memory.sqlite3"
    ask(db, ollama_stub, "caf\u00e9 \u2603")
    with sqlite3.connect(db) as connection:
        rows = connection.execute("SELECT role, content FROM turns ORDER BY id").fetchall()
    assert rows == [("user", "caf\u00e9 \u2603"), ("assistant", "Local reply: caf\u00e9 \u2603")]


def test_resume_sends_previous_turns_to_the_local_model(tmp_path, ollama_stub):
    db = tmp_path / "memory.sqlite3"
    first = ask(db, ollama_stub, "first")
    ask(db, ollama_stub, "second", first["session_id"])
    assert [m["role"] for m in ollama_stub["requests"][-1]["messages"]] == [
        "system", "user", "assistant", "user"]


def test_sessions_can_be_listed_without_ollama(tmp_path, ollama_stub):
    db = tmp_path / "memory.sqlite3"
    first = ask(db, ollama_stub)
    sessions = successful_json(run_cli(db, "sessions", "--json"))
    assert sessions[0]["session_id"] == first["session_id"]


def test_saved_history_can_be_read_without_ollama(tmp_path, ollama_stub):
    db = tmp_path / "memory.sqlite3"
    first = ask(db, ollama_stub)
    turns = successful_json(run_cli(db, "history", first["session_id"], "--json"))
    assert [t["role"] for t in turns] == ["user", "assistant"]


def test_cloud_model_selection_is_refused(tmp_path, ollama_stub):
    result = run_cli(tmp_path / "memory.sqlite3", "--model", "fixture:cloud",
                     "ask", "private prompt", stub=ollama_stub)
    assert result.returncode != 0 and "cloud" in result.stderr.lower()


def test_remote_model_metadata_is_refused(tmp_path, ollama_stub):
    ollama_stub["models"][0]["remote_host"] = "https://example.invalid"
    result = run_cli(tmp_path / "memory.sqlite3", "ask", "private prompt", stub=ollama_stub)
    assert result.returncode != 0 and "local" in result.stderr.lower()


def test_external_api_host_is_refused(tmp_path):
    result = run_cli(tmp_path / "memory.sqlite3", "--base-url", "https://example.invalid",
                     "ask", "private prompt")
    assert result.returncode != 0 and "loopback" in result.stderr.lower()


@pytest.mark.parametrize("mode", ["http_error", "malformed", "incomplete", "oversized", "redirect",
                                 "bad_json", "array", "wrong_role"])
def test_invalid_model_response_does_not_save_a_turn(tmp_path, ollama_stub, mode):
    db = tmp_path / "memory.sqlite3"
    ollama_stub["mode"] = mode
    result = run_cli(db, "ask", "test prompt", stub=ollama_stub)
    assert result.returncode != 0 and "Ollama" in result.stderr, result.stdout + result.stderr
    assert db.exists(), "Storage must remain usable after a model failure"
    with sqlite3.connect(db) as connection:
        count = connection.execute("SELECT COUNT(*) FROM turns").fetchone()[0]
    assert count == 0


def test_unknown_session_is_reported(tmp_path, ollama_stub):
    result = run_cli(tmp_path / "memory.sqlite3", "ask", "test",
                     "--session", "unknown", stub=ollama_stub)
    assert result.returncode != 0 and "session" in result.stderr.lower()


def test_missing_local_model_is_reported(tmp_path, ollama_stub):
    ollama_stub["models"] = []
    result = run_cli(tmp_path / "memory.sqlite3", "ask", "test", stub=ollama_stub)
    assert result.returncode != 0 and "not installed" in result.stderr.lower()


def test_long_prompt_is_refused(tmp_path, ollama_stub):
    result = run_cli(tmp_path / "memory.sqlite3", "ask", "x" * 5000, stub=ollama_stub)
    assert result.returncode != 0 and "4000" in result.stderr


def test_empty_prompt_is_refused(tmp_path, ollama_stub):
    result = run_cli(tmp_path / "memory.sqlite3", "ask", " ", stub=ollama_stub)
    assert result.returncode != 0 and "non-empty" in result.stderr.lower()


def test_proxy_environment_is_not_used_for_local_chat(tmp_path, ollama_stub):
    result = run_cli(tmp_path / "memory.sqlite3", "ask", "test", "--json", stub=ollama_stub,
                     extra_env={"HTTP_PROXY": "http://127.0.0.1:9", "NO_PROXY": ""})
    assert successful_json(result)["reply"] == "Local reply: test"


def test_interactive_chat_can_exit_cleanly(tmp_path, ollama_stub):
    result = run_cli(tmp_path / "memory.sqlite3", "chat", stub=ollama_stub,
                     input_text="hello\n/exit\n")
    assert result.returncode == 0 and "Local reply: hello" in result.stdout


@pytest.mark.skipif(os.name != "nt", reason="Windows console shutdown polling")
def test_interactive_chat_honors_existing_shutdown_request(tmp_path, ollama_stub):
    shutdown_request = tmp_path / "shutdown.request"
    shutdown_request.write_text("stop", encoding="utf-8")
    result = run_cli(
        tmp_path / "memory.sqlite3",
        "--shutdown-request", str(shutdown_request),
        "chat",
        stub=ollama_stub,
    )
    assert result.returncode == 0
    assert "Shutdown requested; closing chat cleanly." in result.stdout


def test_doctor_confirms_local_model_and_storage(tmp_path, ollama_stub):
    result = successful_json(run_cli(tmp_path / "memory.sqlite3", "doctor", "--json",
                                    stub=ollama_stub))
    assert result["local_model_ready"] is True and result["memory_writable"] is True


def test_invalid_inventory_is_reported(tmp_path, ollama_stub):
    ollama_stub["models"] = {"unexpected": "object"}
    result = run_cli(tmp_path / "memory.sqlite3", "doctor", stub=ollama_stub)
    assert result.returncode == 1 and "inventory" in result.stderr


def test_missing_endpoint_port_is_refused(tmp_path):
    result = run_cli(tmp_path / "memory.sqlite3", "--base-url", "http://127.0.0.1", "doctor")
    assert result.returncode == 1 and "port" in result.stderr


def test_unreachable_local_ollama_is_reported(tmp_path):
    result = run_cli(tmp_path / "memory.sqlite3", "--base-url", "http://127.0.0.1:1", "doctor")
    assert result.returncode == 1 and "Cannot reach local Ollama" in result.stderr


def test_another_models_session_is_not_silently_resumed(tmp_path, ollama_stub):
    db = tmp_path / "memory.sqlite3"
    first = ask(db, ollama_stub)
    ollama_stub["models"].append({"name": "fixture:local"})
    result = run_cli(db, "--model", "fixture:local", "ask", "second",
                     "--session", first["session_id"], stub=ollama_stub)
    assert result.returncode == 1 and "another model" in result.stderr
    assert len(ollama_stub["requests"]) == 1


def test_old_context_is_trimmed_in_complete_pairs(tmp_path, ollama_stub):
    db = tmp_path / "memory.sqlite3"
    first = ask(db, ollama_stub, "x" * 3500)
    second = ask(db, ollama_stub, "second", first["session_id"])
    assert second["context_turns"] == 0
    assert [m["role"] for m in ollama_stub["requests"][-1]["messages"]] == ["system", "user"]
    turns = successful_json(run_cli(db, "history", first["session_id"], "--json"))
    assert len(turns) == 4, "Trimming model context must not delete saved history"


def test_unknown_database_version_is_not_migrated(tmp_path, ollama_stub):
    db = tmp_path / "memory.sqlite3"
    with sqlite3.connect(db) as connection:
        connection.execute("PRAGMA user_version = 99")
    result = run_cli(db, "doctor", stub=ollama_stub)
    assert result.returncode == 1 and "Unsupported memory" in result.stderr
    with sqlite3.connect(db) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 99


def test_plain_text_commands_and_empty_sessions(tmp_path, ollama_stub):
    db = tmp_path / "memory.sqlite3"
    assert "No saved sessions" in run_cli(db, "sessions").stdout
    first = ask(db, ollama_stub)
    assert first["session_id"] in run_cli(db, "sessions").stdout
    assert "assistant> Local reply:" in run_cli(db, "history", first["session_id"]).stdout
    assert "Saved locally" in run_cli(db, "ask", "plain", stub=ollama_stub).stdout
    assert "PASS cloud fallback disabled" in run_cli(db, "doctor", stub=ollama_stub).stdout


def test_interactive_empty_input_eof_and_retry_after_error(tmp_path, ollama_stub):
    db = tmp_path / "memory.sqlite3"
    result = run_cli(db, "chat", stub=ollama_stub, input_text="\n")
    assert result.returncode == 0
    result = run_cli(db, "chat", stub=ollama_stub,
                     input_text="x" * 5000 + "\nvalid\n/quit\n")
    assert result.returncode == 0 and "4000" in result.stderr
    assert "Local reply: valid" in result.stdout
    ollama_stub["mode"] = "http_error"
    result = run_cli(db, "chat", stub=ollama_stub, input_text="error\n/exit\n")
    assert result.returncode == 0 and "Ollama HTTP error" in result.stderr
    with sqlite3.connect(db) as connection:
        assert connection.execute("SELECT COUNT(*) FROM turns").fetchone()[0] == 2


def test_custom_vocabulary_reaches_model_without_exceeding_context(tmp_path, ollama_stub):
    words = tmp_path / "words.json"
    words.write_text(json.dumps({"entries": [
        {"term": "florble", "definition": "A project-specific red widget."},
        {"term": "other", "definition": "Unrelated term."},
    ]}), encoding="utf-8")
    result = successful_json(run_cli(tmp_path / "memory.sqlite3", "--vocabulary", str(words),
                                    "ask", "Explain florble", "--json", stub=ollama_stub))
    system = ollama_stub["requests"][-1]["messages"][0]["content"]
    assert "red widget" in system and "Unrelated term" not in system
    assert result["vocabulary_entries_used"] == 1
    assert sum(len(m["content"]) for m in ollama_stub["requests"][-1]["messages"]) <= 6000

def test_vocabulary_can_be_disabled_and_missing_file_is_reported(tmp_path, ollama_stub):
    result = successful_json(run_cli(tmp_path / "memory.sqlite3", "--no-vocabulary",
                                    "ask", "Explain vocabulary", "--json", stub=ollama_stub))
    assert result["vocabulary_entries_used"] == 0
    failed = run_cli(tmp_path / "missing.sqlite3", "--vocabulary", str(tmp_path / "missing.json"),
                     "ask", "hello", stub=ollama_stub)
    assert failed.returncode == 1 and "Vocabulary file" in failed.stderr
    assert not (tmp_path / "missing.sqlite3").exists()

def test_doctor_checks_vocabulary_and_database_integrity(tmp_path, ollama_stub):
    result = successful_json(run_cli(tmp_path / "memory.sqlite3", "doctor", "--json", stub=ollama_stub))
    assert result["memory_integrity"] == "ok"
    assert result["vocabulary_entries"] == 6

def test_doctor_reports_broken_history_relationships(tmp_path, ollama_stub):
    db = tmp_path / "memory.sqlite3"
    ask(db, ollama_stub)
    with sqlite3.connect(db) as connection:
        connection.execute("UPDATE turns SET session_id = 'missing'")
    failed = run_cli(db, "doctor", stub=ollama_stub)
    assert failed.returncode == 1 and "integrity check failed" in failed.stderr


def test_memory_v1_migrates_to_v2_without_losing_history(tmp_path):
    from prometheus_assistant.memory import MemoryStore
    db = tmp_path / "memory.sqlite3"
    with sqlite3.connect(db) as connection:
        connection.executescript("""
            CREATE TABLE sessions (session_id TEXT PRIMARY KEY, model TEXT NOT NULL, created_at TEXT NOT NULL);
            CREATE TABLE turns (
                id INTEGER PRIMARY KEY,
                session_id TEXT NOT NULL REFERENCES sessions(session_id),
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            INSERT INTO sessions VALUES ('legacy', 'llama3.2:1b', '2026-01-01T00:00:00+00:00');
            INSERT INTO turns (session_id, role, content, created_at)
                VALUES ('legacy', 'user', 'preserve me', '2026-01-01T00:00:00+00:00');
            PRAGMA user_version = 1;
        """)
    with MemoryStore(db) as memory:
        assert memory.history("legacy")[0]["content"] == "preserve me"
        memory.remember(
            "preference", "response.detail", "step-by-step",
            source_type="user_statement", source_ref="session:legacy",
            retention="until_replaced",
        )
        rows = memory.knowledge(kind="preference")
        assert rows[0]["value"] == "step-by-step"
        assert rows[0]["source_type"] == "user_statement"
    with sqlite3.connect(db) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 2


def test_until_replaced_knowledge_preserves_provenance_history(tmp_path):
    from prometheus_assistant.memory import MemoryStore
    with MemoryStore(tmp_path / "memory.sqlite3") as memory:
        first = memory.remember(
            "decision", "research.mode", "local-only",
            source_type="user_statement", source_ref="turn:1",
            retention="until_replaced",
        )
        second = memory.remember(
            "decision", "research.mode", "online-when-requested",
            source_type="user_statement", source_ref="turn:2",
            retention="until_replaced",
        )
        active = memory.knowledge(kind="decision", subject="research.mode")
        all_rows = memory.knowledge(
            kind="decision", subject="research.mode", include_superseded=True
        )
        assert [row["id"] for row in active] == [second]
        assert len(all_rows) == 2
        assert all_rows[0]["id"] == first and all_rows[0]["superseded_at"] is not None


@pytest.mark.parametrize("field,value", [
    ("kind", "unknown"),
    ("retention", "forever"),
    ("confidence", 1.1),
])
def test_structured_knowledge_rejects_invalid_metadata(tmp_path, field, value):
    from prometheus_assistant.memory import MemoryStore
    kwargs = dict(
        kind="fact", subject="project.name", value="Prometheus",
        source_type="user_statement", source_ref="turn:1",
        retention="persistent", confidence=1.0,
    )
    kwargs[field] = value
    with MemoryStore(tmp_path / "memory.sqlite3") as memory:
        with pytest.raises(ValueError):
            memory.remember(**kwargs)


def test_explicit_remember_command_writes_provenance_and_replaces(tmp_path):
    from prometheus_assistant.cli import _remember_command, _memory_rows
    from prometheus_assistant.memory import MemoryStore
    with MemoryStore(tmp_path/"m.sqlite3") as memory:
        assert "Remembered" in _remember_command(memory,"abc","preference | response style | concise")
        _remember_command(memory,"abc","preference | response style | detailed")
        active=_memory_rows(memory,"response")
        all_rows=memory.knowledge(kind="preference",subject="response style",include_superseded=True)
    assert len(active)==1 and active[0]["value"]=="detailed"
    assert active[0]["source_ref"]=="session:abc"
    assert len(all_rows)==2 and all_rows[0]["superseded_at"] is not None


def test_explicit_remember_command_rejects_invalid_kind(tmp_path):
    from prometheus_assistant.cli import _remember_command
    from prometheus_assistant.memory import MemoryStore
    with MemoryStore(tmp_path/"m.sqlite3") as memory:
        with pytest.raises(ValueError,match="KIND"):
            _remember_command(memory,"abc","secret | password | nope")
