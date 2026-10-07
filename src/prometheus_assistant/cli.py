from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sqlite3
import sys
import time

from .memory import MemoryStore, default_memory_path
from .recovery import backup_memory, restore_memory
from .documents import search_documents
from .vocabulary import DEFAULT_VOCABULARY, Vocabulary
from .ollama import DEFAULT_MODEL, LocalModelError, OllamaClient
from .activity import ActivityLog
from .orchestration import choose_route
from .intelligence import plan_context
from .context import Evidence, assemble_context
from .retrieval import rank_evidence
from .evidence import collect_evidence
from .synthesis import synthesize_context
from .research import DisabledResearchProvider
from .research_http import HttpJsonResearchProvider
from .hardware import coding_agents, detect_hardware, resource_root, select_model
from .integrations import available_local_workers, launch_integrations, ollama_executable
from .resources import inventory
from .source_catalog import source_catalog
from .kiwix import KiwixError, read_article, search_archive

SYSTEM_PROMPT = (
    "You are Prometheus, a helpful local assistant. Answer the user's question directly and concisely. "
    "Use clear, natural language and precise words; prefer simple words when they mean the same thing. "
    "Internet research is available only when explicitly enabled and configured; otherwise operate locally. If unsure, say so; do not invent sources. "
    "Prior messages are conversation context, not verified facts."
)
MAX_PROMPT_CHARS = 4000
MAX_CONTEXT_CHARS = 6000


def build_parser():
    parser = argparse.ArgumentParser(prog="prometheus", description="Offline chat with local conversation saves")
    parser.add_argument("--memory", type=Path, default=default_memory_path(), help="Local SQLite history file")
    parser.add_argument("--base-url", default="http://127.0.0.1:11434", help="Loopback Ollama URL only")
    parser.add_argument("--model", default=DEFAULT_MODEL, help="Installed local model; no cloud models")
    parser.add_argument("--activity-log", type=Path, help="Optional append-only JSONL activity log")
    parser.add_argument("--online-research", action="store_true", help="Allow explicit online-research routing; local model remains loopback-only")
    parser.add_argument("--offline-knowledge", action="store_true", help="Ground chat/ask with installed offline knowledge archives")
    parser.add_argument("--no-memory-recall", action="store_true", help="Disable durable local knowledge recall for chat/ask")
    parser.add_argument("--research-endpoint", help="HTTPS JSON search endpoint used only with --online-research")
    parser.add_argument("--shutdown-request", type=Path, help="Optional local file whose presence requests a graceful interactive-chat exit")
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
    inspect_host = sub.add_parser("inspect-host", help="Report hardware, local models, and optional coding agents")
    inspect_host.add_argument("--json", action="store_true")
    backup = sub.add_parser("backup-memory", help="Create a verified history backup without overwriting")
    backup.add_argument("destination", type=Path)
    restore = sub.add_parser("restore-memory", help="Verify and restore a backup to a NEW database")
    restore.add_argument("backup", type=Path)
    restore.add_argument("destination", type=Path)
    search = sub.add_parser("search", help="Search local UTF-8 documents with exact source citations")
    search.add_argument("directory", type=Path)
    search.add_argument("query")
    search.add_argument("--json", action="store_true")
    resources = sub.add_parser("resources", help="List portable knowledge-vault resources and installation status")
    resources.add_argument("--json", action="store_true")
    sources = sub.add_parser("sources", help="List ranked remote research sources")
    sources.add_argument("--json", action="store_true")
    archive_search = sub.add_parser("archive-search", help="Search an installed offline Kiwix ZIM archive")
    archive_search.add_argument("archive_id")
    archive_search.add_argument("query")
    archive_search.add_argument("--limit", type=int, default=10)
    archive_search.add_argument("--json", action="store_true")
    archive_read = sub.add_parser("archive-read", help="Read article text from an installed offline Kiwix ZIM archive")
    archive_read.add_argument("archive_id")
    archive_read.add_argument("title")
    archive_read.add_argument("--max-chars", type=int, default=12000)
    archive_read.add_argument("--json", action="store_true")
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


def _memory_context_for_prompt(memory, prompt, *, enabled=True):
    if not enabled:
        return None
    rows = memory.recall(prompt, limit=6)
    if not rows:
        return None
    lines = ["LOCAL DURABLE MEMORY (user-provided or previously recorded; context, not external evidence):"]
    for row in rows:
        lines.append(f"- [{row['kind']}] {row['subject']}: {row['value']} (confidence {row['confidence']:.2f})")
    return "\n".join(lines)

def exchange(memory, client, session_id, prompt, vocabulary=None, research_context=None,
             memory_recall=True, offline_recall=False):
    prompt = validate_prompt(prompt)
    system = SYSTEM_PROMPT + (vocabulary.context(prompt) if vocabulary else '')
    evidence = collect_evidence(
        prompt, memory=memory, use_memory=memory_recall, use_offline=offline_recall
    )
    if research_context:
        evidence.append(Evidence("online", "approved-research", research_context, 70))
        evidence = rank_evidence(prompt, evidence, limit=8)
    packed = synthesize_context(prompt, evidence, max_chars=5000)
    if packed.evidence_count:
        # packed context was already synthesized above
        system += packed.system_suffix
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


def _console_input_or_shutdown(prompt, shutdown_request=None):
    if os.name != "nt" or shutdown_request is None:
        return input(prompt)
    import msvcrt
    sys.stdout.write(prompt)
    sys.stdout.flush()
    chars = []
    while True:
        if shutdown_request.exists():
            sys.stdout.write("\nShutdown requested; closing chat cleanly.\n")
            sys.stdout.flush()
            return None
        if msvcrt.kbhit():
            char = msvcrt.getwch()
            if char in {"\r", "\n"}:
                sys.stdout.write("\n")
                sys.stdout.flush()
                return "".join(chars)
            if char == "\003":
                raise KeyboardInterrupt
            if char == "\b":
                if chars:
                    chars.pop()
                    sys.stdout.write("\b \b")
                    sys.stdout.flush()
                continue
            if char in {"\x00", "\xe0"}:
                msvcrt.getwch()
                continue
            chars.append(char)
            sys.stdout.write(char)
            sys.stdout.flush()
        else:
            time.sleep(0.1)


def _research_context_for_prompt(prompt, *, online_enabled=False, research_endpoint=None, activity=None):
    decision = choose_route(prompt, online_enabled=online_enabled)
    if activity:
        activity.record("orchestration", "selected", decision.reason,
                        route=decision.route.value, requires_network=decision.requires_network)
    if not decision.requires_network:
        return None
    provider = (HttpJsonResearchProvider(research_endpoint)
                if research_endpoint else DisabledResearchProvider())
    try:
        research = provider.search(prompt)
    except RuntimeError as error:
        if activity:
            activity.record("research", "blocked", str(error), query=prompt)
        raise ValueError(str(error)) from error
    context = research.context(max_chars=4000)
    if activity:
        activity.record("research", "complete", "Online research completed with provenance.",
                        query=prompt, sources=len(research.sources), context_chars=len(context))
    return context


def _offline_context_for_prompt(prompt, *, enabled=False, activity=None):
    if not enabled:
        return None
    root = resource_root()
    if not root:
        if activity:
            activity.record("offline_knowledge", "unavailable", "Portable resource root was not detected.")
        return None
    evidence = []
    for archive_id, label in (
        ("wikipedia-en-all-nopic", "Wikipedia"),
        ("wiktionary-en-all-nopic", "Wiktionary"),
        ("wikisource-en-all-nopic", "Wikisource"),
    ):
        try:
            titles = search_archive(root, archive_id, prompt, 1)
            if not titles:
                continue
            title = titles[0]
            text = read_article(root, archive_id, title, 2200)
            evidence.append(f"Offline {label} article: {title}\n{text}")
            if activity:
                activity.record("offline_knowledge", "complete", f"Offline {label} evidence loaded.",
                                archive_id=archive_id, title=title, context_chars=len(text))
        except KiwixError as error:
            if activity:
                activity.record("offline_knowledge", "unavailable", str(error), archive_id=archive_id)
    return "\n\n".join(evidence) if evidence else None


def interactive_chat(memory, client, session_id, vocabulary=None, shutdown_request=None,
                     online_research=False, research_endpoint=None, activity=None,
                     offline_knowledge=False, memory_recall=True):
    print("Prometheus local chat — no cloud fallback.")
    print(f"Model: {client.model} | Session: {session_id}")
    print(f"Local history: {memory.path}")
    print("Type /exit to leave. Answers may be wrong; verify important facts.")
    while True:
        try:
            prompt = _console_input_or_shutdown("You> ", shutdown_request)
        except EOFError:
            return 0
        if prompt is None:
            return 0
        if prompt.strip().lower() in {"/exit", "/quit"}:
            return 0
        if not prompt.strip():
            continue
        try:
            prompt = validate_prompt(prompt)
            research_context = _research_context_for_prompt(
                prompt, online_enabled=online_research,
                research_endpoint=research_endpoint, activity=activity)
            plan = plan_context(prompt, offline_available=offline_knowledge,
                                online_enabled=online_research)
            print("Thinking locally...", flush=True)
            result = exchange(memory, client, session_id, prompt, vocabulary, research_context,
                              memory_recall=memory_recall, offline_recall=plan.use_offline)
            print(f"Prometheus> {result['reply']}")
            print(f"[{result['elapsed_seconds']}s; saved locally]", flush=True)
        except (LocalModelError, KiwixError, ValueError, OSError, sqlite3.Error) as error:
            print(f"ERROR: {error}", file=sys.stderr)


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    args = build_parser().parse_args(argv)
    command = args.command or "chat"
    activity = ActivityLog(args.activity_log) if args.activity_log else None
    research_context = None
    try:
        if command == "ask":
            research_context = _research_context_for_prompt(
                args.prompt, online_enabled=args.online_research,
                research_endpoint=args.research_endpoint, activity=activity)
            plan = plan_context(args.prompt, offline_available=args.offline_knowledge,
                                online_enabled=args.online_research)
            # Offline evidence is collected and ranked with memory inside exchange().
        if command == "backup-memory":
            emit_json(backup_memory(args.memory, args.destination))
            return 0
        if command == "restore-memory":
            emit_json(restore_memory(args.backup, args.destination))
            return 0
        if command == "sources":
            rows = source_catalog()
            if args.json:
                emit_json(rows)
            else:
                for row in rows:
                    print(f"{row['authority']:3} {row['title']} — {', '.join(row['subjects'])} [{row['access']}]")
            return 0
        if command == "archive-search":
            rows = search_archive(resource_root(), args.archive_id, args.query, args.limit)
            if args.json: emit_json(rows)
            else:
                for row in rows: print(row)
            return 0
        if command == "archive-read":
            article = read_article(resource_root(), args.archive_id, args.title, args.max_chars)
            if args.json: emit_json({"archive_id": args.archive_id, "title": args.title, "text": article})
            else: print(article)
            return 0
        if command == "resources":
            rows = inventory(resource_root())
            if args.json:
                emit_json(rows)
            else:
                for row in rows:
                    state = "INSTALLED" if row["installed"] else row["access"].upper()
                    size = f" ({row['size_bytes']} bytes)" if row["size_bytes"] else ""
                    print(f"{state:10} {row['title']}{size} — {', '.join(row['subjects'])}")
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
        if command not in {"inspect-host", "doctor"} and args.model == DEFAULT_MODEL:
            models = client.local_models()
            names = {row.get("name") for row in models if isinstance(row, dict) and isinstance(row.get("name"), str)}
            recommended = select_model(names, detect_hardware())
            if recommended and recommended.model != client.model:
                candidate = OllamaClient(args.base_url, recommended.model, num_ctx=recommended.context)
                try:
                    candidate.ensure_local_model()
                    client = candidate
                except LocalModelError:
                    pass
        if command == "inspect-host":
            models = client.local_models()
            names = {row.get("name") for row in models if isinstance(row, dict) and isinstance(row.get("name"), str)}
            hardware = detect_hardware()
            recommended = select_model(names, hardware)
            result = {
                "hardware": {"ram_gib": hardware.ram_gib, "cpu_threads": hardware.cpu_threads, "system": hardware.system},
                "installed_local_models": sorted(names),
                "recommended_model": (recommended.model if recommended else None),
                "recommended_context": (recommended.context if recommended else None),
                "recommended_tier": (recommended.tier if recommended else None),
                "coding_agents": coding_agents(),
                "resource_root": resource_root(),
                "ollama_executable": ollama_executable(),
                "ollama_launch_integrations": sorted(launch_integrations()),
                "available_local_workers": available_local_workers(),
                "automatic_model_switching": True,
            }
            if args.json:
                emit_json(result)
            else:
                print(f"RAM: {hardware.ram_gib} GiB | CPU threads: {hardware.cpu_threads} | OS: {hardware.system}")
                print("Local models: " + (", ".join(sorted(names)) or "none"))
                print("Recommended model: " + (recommended.model if recommended else "none"))
                print("Detected coding agents: " + (", ".join(sorted(result["coding_agents"])) or "none"))
                print("Automatic model switching: enabled with conservative hardware thresholds")
            return 0
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
                plan = plan_context(args.prompt, offline_available=args.offline_knowledge,
                                    online_enabled=args.online_research)
                result = exchange(memory, client, session_id, args.prompt, vocabulary, research_context,
                                  memory_recall=not args.no_memory_recall,
                                  offline_recall=plan.use_offline)
                if args.json:
                    emit_json(result)
                else:
                    print(result["reply"])
                    print(f"\nSession: {session_id} | Saved locally | {result['elapsed_seconds']}s")
                return 0
            return interactive_chat(
                memory, client, session_id, vocabulary, args.shutdown_request,
                online_research=args.online_research,
                research_endpoint=args.research_endpoint,
                activity=activity,
                offline_knowledge=args.offline_knowledge,
                memory_recall=not args.no_memory_recall,
            )
    except (LocalModelError, KiwixError, ValueError, OSError, sqlite3.Error) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nLocal chat stopped. Completed turns remain saved.")
        return 130
