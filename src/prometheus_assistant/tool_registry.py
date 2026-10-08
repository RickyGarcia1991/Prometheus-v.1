"""Typed tool registry for Prometheus Core."""
from dataclasses import dataclass
from .guardrails import ToolRequest

@dataclass(frozen=True)
class ToolSpec:
    worker: str
    name: str
    description: str
    executor: object
    mutates_state: bool = False
    external_network: bool = False
    command_execution: bool = False

    @property
    def key(self):
        return self.worker, self.name

    def request(self, summary):
        return ToolRequest(self.worker, self.name, summary,
            mutates_state=self.mutates_state,
            external_network=self.external_network,
            command_execution=self.command_execution)

class ToolRegistry:
    def __init__(self, tools=()):
        self._tools = {}
        for tool in tools:
            self.register(tool)

    def register(self, tool):
        if not tool.worker.strip() or not tool.name.strip() or not tool.description.strip():
            raise ValueError("Tool metadata must be non-empty.")
        if tool.key in self._tools:
            raise ValueError("Duplicate tool registration.")
        self._tools[tool.key] = tool

    def get(self, worker, name):
        if (worker, name) not in self._tools:
            raise ValueError(f"Unknown tool: {worker}/{name}")
        return self._tools[(worker, name)]

    def requests_from_plan(self, rows):
        requests = []
        for row in rows:
            if not isinstance(row, dict) or set(row) != {"worker", "tool", "summary"}:
                raise ValueError("Invalid planned tool schema.")
            summary = row["summary"]
            if not isinstance(summary, str) or not summary.strip() or len(summary) > 300:
                raise ValueError("Invalid tool summary.")
            requests.append(self.get(row["worker"], row["tool"]).request(summary.strip()))
        return tuple(requests)

    def executors(self):
        return {key: spec.executor for key, spec in self._tools.items()}

    def catalog(self):
        if not self._tools:
            return "(no tools registered)"
        return "\n".join(
            f"- {x.worker}/{x.name}: {x.description}; state_change={x.mutates_state}; "
            f"network={x.external_network}; command={x.command_execution}"
            for x in self._tools.values())
