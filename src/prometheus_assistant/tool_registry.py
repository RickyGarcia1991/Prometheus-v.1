"""Typed, bounded tool registry for Prometheus Core."""
from dataclasses import dataclass
from types import MappingProxyType
from .guardrails import ToolRequest

@dataclass(frozen=True)
class ArgSpec:
    kind: type = str
    required: bool = True
    max_length: int = 400

@dataclass(frozen=True)
class ToolSpec:
    worker: str
    name: str
    description: str
    executor: object
    arguments: object = None
    mutates_state: bool = False
    external_network: bool = False
    command_execution: bool = False

    @property
    def key(self): return self.worker, self.name

    def validate_arguments(self, value):
        schema=dict(self.arguments or {})
        if value is None: value={}
        if not isinstance(value,dict) or set(value)-set(schema):
            raise ValueError("Invalid tool arguments.")
        out={}
        for name,spec in schema.items():
            if name not in value:
                if spec.required: raise ValueError(f"Missing tool argument: {name}")
                continue
            item=value[name]
            if not isinstance(item,spec.kind) or isinstance(item,bool) and spec.kind is int:
                raise ValueError(f"Invalid tool argument type: {name}")
            if isinstance(item,str):
                item=item.strip()
                if not item or len(item)>spec.max_length: raise ValueError(f"Invalid tool argument: {name}")
            out[name]=item
        return MappingProxyType(out)

    def request(self, summary, arguments=None):
        return ToolRequest(self.worker,self.name,summary,self.validate_arguments(arguments),
            mutates_state=self.mutates_state,external_network=self.external_network,
            command_execution=self.command_execution)

class ToolRegistry:
    def __init__(self,tools=()):
        self._tools={}
        for tool in tools:self.register(tool)
    def register(self,tool):
        if not tool.worker.strip() or not tool.name.strip() or not tool.description.strip(): raise ValueError("Tool metadata must be non-empty.")
        if tool.key in self._tools: raise ValueError("Duplicate tool registration.")
        self._tools[tool.key]=tool
    def get(self,worker,name):
        if (worker,name) not in self._tools: raise ValueError(f"Unknown tool: {worker}/{name}")
        return self._tools[(worker,name)]
    def requests_from_plan(self,rows):
        requests=[]
        for row in rows:
            if not isinstance(row,dict) or set(row)!={"worker","tool","summary","arguments"}: raise ValueError("Invalid planned tool schema.")
            summary=row["summary"]
            if not isinstance(summary,str) or not summary.strip() or len(summary)>300: raise ValueError("Invalid tool summary.")
            spec = self.get(row["worker"], row["tool"])
            # Model prose is retained in the raw plan, never promoted to the
            # description of an observed tool result or an approval action.
            requests.append(spec.request(spec.description[:300], row["arguments"]))
        return tuple(requests)
    def executors(self): return RegisteredExecutors(self._tools)
    def structured_catalog(self):
        """Serializable discovery contract; metadata never grants execution rights."""
        types = {str: 'string', int: 'integer', bool: 'boolean', float: 'number', dict: 'object', list: 'array'}
        rows = []
        for key, tool in sorted(self._tools.items()):
            properties, required = {}, []
            for name, spec in sorted(dict(tool.arguments or {}).items()):
                if spec.kind not in types:
                    raise ValueError('Tool argument type has no JSON schema representation.')
                properties[name] = {'type': types[spec.kind]}
                if spec.kind is str:
                    properties[name].update(minLength=1, maxLength=spec.max_length)
                if spec.required:
                    required.append(name)
            rows.append({'worker': key[0], 'name': key[1], 'description': tool.description,
                         'inputSchema': {'type': 'object', 'properties': properties,
                                         'required': required, 'additionalProperties': False},
                         'risk': {'mutates_state': tool.mutates_state, 'external_network': tool.external_network,
                                  'command_execution': tool.command_execution}})
        return rows
    def catalog(self):
        if not self._tools:return "(no tools registered)"
        rows=[]
        for x in self._tools.values():
            args={name:{"type":spec.kind.__name__,"required":spec.required,"max_length":spec.max_length} for name,spec in dict(x.arguments or {}).items()}
            rows.append(f"- {x.worker}/{x.name}: {x.description}; arguments={args}; state_change={x.mutates_state}; network={x.external_network}; command={x.command_execution}")
        return "\n".join(rows)


class RegisteredExecutors(dict):
    """Trusted Python registrations accompanying the concrete execution map."""
    def __init__(self, specs):
        self.specs = MappingProxyType(dict(specs))
        super().__init__((key, spec.executor) for key, spec in specs.items())

    def validate(self, requests):
        from .application_policy import registered_decision
        for request in requests:
            spec = self.specs.get((request.worker, request.tool))
            if spec is None:
                raise ValueError('Unmapped registered capability.')
            registered_decision(spec, request)
