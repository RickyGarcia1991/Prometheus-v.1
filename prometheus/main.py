"""Minimal CLI entry point."""
from prometheus.cognition.stub import StubModel
from prometheus.core.engine import PrometheusEngine
from prometheus.memory.in_memory import InMemoryStore

def main() -> None:
    engine = PrometheusEngine(StubModel(), InMemoryStore())
    print("Prometheus local shell. Type 'exit' to stop.")
    while True:
        try:
            text = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if text.lower() in {"exit", "quit"}:
            break
        if text:
            print(engine.respond(text))

if __name__ == "__main__":
    main()
