from __future__ import annotations

import argparse
import json
from pathlib import Path
import sqlite3
import sys
import time

from .memory import MemoryStore, default_memory_path
from .recovery import backup_memory, restore_memory
from .documents import search_documents
from .vocabulary import DEFAULT_VOCABULARY, Vocabulary
from .ollama import DEFAULT_MODEL, LocalModelError, OllamaClient

SYSTEM_PROMPT = (
    "You are Prometheus, a helpful local assistant. Answer the user's question directly and concisely. "
    "Use clear, natural language and precise words; prefer simple words when they mean the same thing. "
    "You have no internet access or tools. If unsure, say so; do not invent sources. "
    "Prior messages are conversation context, not verified facts."
)
MAX_PROMPT_CHARS = 4000
MAX_CONTEXT_CHARS = 6000


def build_parser():
    parser = argparse.ArgumentParser(prog="prometheus", description="Offline chat with local conversation saves")
    parser.add_argument("--memory", type=Path, default=default_memory_path(), help="Local SQLite history file")
    parser.add_argument("--base-url", default="http://127.0.0.1:11434", help="Loopback Ollama URL only")
    parser.add_argument("--model", default=DEFAULT_MODEL, help="Installed local model; no cloud models")
    vocabulary = parser.add_mutually_exclusive_group()
    vocabulary.add_argument("--vocabulary", type=Path, help="Custom local glossary JSON file")
    vocabulary.add_argument("--no-vocabulary", action="store_true", help="Disable glossary guidance")
    sub = parser.add_subparsers(dest="command")
    chat = sub.add_parser("chat", help="Start local interactive chat; /exit to leave")
    chat.add_argument("--session", help="Explicitly resume a saved session ID")
    ask = sub.add_parser("ask", help="Ask once, saving a successful exchange")
    ask.add_argument("prompt")
    ask.add_argument("--session")
    ask.add_argument("--json", action="store_true")
    sessions = sub.add_parser("sessions", help="List saved local conversations")
    sessions.add_argument("--json", action="store_true")
    history = sub.add_parser("history", help="Read a saved local conversation")
    history.add_argument("session_id")
    history.add_argument("--json", action="store_true")
    doctor = sub.add_parser("doctor", help="Check local model and local storage")
    doctor.add_argument("--json", action="store_true")
    backup = sub.add_parser("backup-memory", help="Create a verified history backup without overwriting")
    backup.add_argument("destination", type=Path)
    restore = sub.add_parser("restore-memory", help="Verify and restore a backup to a NEW database")
    restore.add_argument("backup", type=Path)
    restore.add_argument("destination", type=Path)
    search = sub.add_parser("search", help="Search local UTF-8 documents with exact source citations")
    search.add_argument("directory", type=Path)
    search.add_argument("query")
    search.add_argument("--json", action="store_true")
    return parser


def emit_json(value):
    print(json.dumps(value, ensure_ascii=False, indent=2))


def validate_prompt(prompt):
    prompt = prompt.strip()
    if not prompt:
        raise ValueError("Please enter a non-empty prompt.")
    if len(prompt) > MAX_PROMPT_CHARS:
        raise ValueError("Prompts are limited to 4000 characters on this low-memory setup.")
    return prompt


def selected_session(memory, requested, model):
    if requested:
        session = memory.session(requested)
        if session["model"] != model:
            raise ValueError("This session used another model. Select that model explicitly before resuming.")
        return requested
    return memory.create_session(model)


def exchange(memory, client, session_id, prompt, vocabulary=None):
    prompt = validate_prompt(prompt)
    system = SYSTEM_PROMPT + (vocabulary.context(prompt) if vocabulary else '')
    recent = memory.history(session_id, limit=8)
    budget = MAX_CONTEXT_CHARS - len(system) - len(prompt)
    selected = []
    for index in range(len(recent) - 2, -1, -2):
        pair = recent[index:index + 2]
        size = sum(len(turn["content"]) for turn in pair)
        if size > budget:
            break
        selected[0:0] = pair
        budget -= size
    messages = [{"role": "system", "content": system}]
    messages.extend({"role": turn["role"], "content": turn["content"]} for turn in selected)
    messages.append({"role": "user", "content": prompt})
    started = time.perf_counter()
    reply, tokens = client.chat(messages)
    elapsed = time.perf_counter() - started
    memory.save_exchange(session_id, prompt, reply)
    return {"session_id": session_id, "model": client.model, "reply": reply,
            "elapsed_seconds": round(elapsed, 2), "generated_tokens": tokens,
            "context_turns": len(selected), "vocabulary_entries_used": (system.count('\n{'))}


def interactive_chat(memory, client, session_id, vocabulary=None):
    print("Prometheus local chat — no cloud fallback.")
    print(f"Model: {client.model} | Session: {session_id}")
    print(f"Local history: {memory.path}")
    print("Type /exit to leave. Answers may be wrong; verify important facts.")
    while True:
        try:
            prompt = input("You> ")
        except EOFError:
            return 0
        if prompt.strip().lower() in {"/exit", "/quit"}:
            return 0
        if not prompt.strip():
            continue
        try:
            prompt = validate_prompt(prompt)
            print("Thinking locally...", flush=True)
            result = exchange(memory, client, session_id, prompt, vocabulary)
            print(f"Prometheus> {result['reply']}")
            print(f"[{result['elapsed_seconds']}s; saved locally]", flush=True)
        except (LocalModelError, ValueError, OSError, sqlite3.Error) as error:
            print(f"ERROR: {error}", file=sys.stderr)


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    args = build_parser().parse_args(argv)
    command = args.command or "chat"
    try:
        if command == "backup-memory":
            emit_json(backup_memory(args.memory, args.destination))
            return 0
        if command == "restore-memory":
            emit_json(restore_memory(args.backup, args.destination))
            return 0
        if command == "search":
            result = search_documents(args.directory, args.query)
            if args.json:
                emit_json(result)
            else:
                for row in result['results']:
                    print(f"{row['source']}:{row['line']} — {row['excerpt']}")
                if not result['results']:
                    print('No matching evidence found.')
                if result['skipped_files']:
                    print(f"Skipped {len(result['skipped_files'])} unreadable or oversized files.")
            return 0
        if command in {"sessions", "history"}:
            with MemoryStore(args.memory) as memory:
                if command == "sessions":
                    rows = memory.sessions()
                    if args.json:
                        emit_json(rows)
                    else:
                        for row in rows:
                            print(f"{row['session_id']}  {row['model']}  {row['created_at']}  {row['turn_count']} turns")
                        if not rows:
                            print("No saved sessions yet.")
                else:
                    rows = memory.history(args.session_id)
                    if args.json:
                        emit_json(rows)
                    else:
                        for row in rows:
                            print(f"{row['role']}> {row['content']}")
            return 0

        if command == "ask":
            validate_prompt(args.prompt)
        vocabulary = Vocabulary()
        if not args.no_vocabulary:
            path = args.vocabulary or DEFAULT_VOCABULARY
            if args.vocabulary is not None or path.exists():
                vocabulary = Vocabulary.load(path)
        client = OllamaClient(args.base_url, args.model)
        client.ensure_local_model()
        with MemoryStore(args.memory) as memory:
            if command == "doctor":
                result = {"local_model_ready": True, "memory_writable": True,
                          "model": client.model, "endpoint": client.base_url,
                          "memory_path": str(memory.path), "cloud_fallback": False,
                          "memory_integrity": memory.db.execute("PRAGMA integrity_check").fetchone()[0],
                          "vocabulary_entries": len(vocabulary.entries)}
                if result["memory_integrity"] != "ok" or memory.db.execute("PRAGMA foreign_key_check").fetchone():
                    raise ValueError("Local history integrity check failed; restore from a verified backup.")
                if args.json:
                    emit_json(result)
                else:
                    print(f"PASS local model: {client.model}")
                    print(f"PASS local history: {memory.path}")
                    print("PASS cloud fallback disabled")
                    print("PASS local history integrity")
                    print(f"PASS vocabulary: {len(vocabulary.entries)} entries")
                return 0
            session_id = selected_session(memory, getattr(args, "session", None), client.model)
            if command == "ask":
                result = exchange(memory, client, session_id, args.prompt, vocabulary)
                if args.json:
                    emit_json(result)
                else:
                    print(result["reply"])
                    print(f"\nSession: {session_id} | Saved locally | {result['elapsed_seconds']}s")
                return 0
            return interactive_chat(memory, client, session_id, vocabulary)
    except (LocalModelError, ValueError, OSError, sqlite3.Error) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nLocal chat stopped. Completed turns remain saved.")
        return 130
