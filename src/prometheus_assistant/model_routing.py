"""Task-sensitive local helper selection. Advertised roles are not accuracy scores."""
from __future__ import annotations
import re
from .hardware import select_model

ROLE_MODELS = {
    'general': ('qwen3:1.7b', 'qwen3.5:0.8b', 'hermes3:3b', 'qwen3:0.6b', 'llama3.2:1b-instruct-q4_K_M', 'llama3.2:1b', 'qwen2.5-coder:0.5b', 'gemma3:270m'),
    'coding': ('qwen2.5-coder:1.5b', 'qwen2.5-coder:0.5b', 'gpt-oss:20b', 'qwen3:1.7b', 'qwen3.5:0.8b', 'qwen3:0.6b'),
    'reasoning': ('gpt-oss:20b', 'qwen3:1.7b', 'qwen3.5:0.8b', 'hermes3:3b', 'qwen3:0.6b', 'llama3.2:1b'),
    'writing': ('hermes3:3b', 'qwen3:1.7b', 'qwen3.5:0.8b', 'qwen3:0.6b', 'llama3.2:1b', 'qwen2.5-coder:0.5b'),
    'translation': ('qwen3:1.7b', 'qwen3.5:0.8b', 'qwen3:0.6b', 'hermes3:3b', 'llama3.2:1b', 'qwen2.5-coder:0.5b', 'gemma3:270m'),
    'vision': ('qwen3-vl:2b', 'qwen3.5:0.8b'),
}


def infer_role(prompt, task='general', *, image=False):
    if image:
        return 'vision'
    if task == 'coding':
        return 'coding'
    words = set(re.findall(r'[a-z+#]+', prompt.lower()))
    if words & {'code', 'coding', 'debug', 'debugging', 'function', 'programming', 'python',
                'javascript', 'typescript', 'compiler', 'sql', 'script', 'refactor', 'rust',
                'docker', 'api', 'c++', 'c#', 'java', 'arduino', 'html', 'css'}:
        return 'coding'
    if words & {'translate', 'translation', 'traducir', 'traduce', 'multilingual'}:
        return 'translation'
    if words & {'proof', 'prove', 'reasoning', 'derive', 'analyze', 'analyse', 'compare', 'solve'}:
        return 'reasoning'
    if words & {'write', 'rewrite', 'draft', 'summarize', 'summarise', 'story', 'poem', 'edit'}:
        return 'writing'
    return 'general'


def choose_helper(installed, hardware, role, resident_model_gib=0):
    if role not in ROLE_MODELS:
        raise ValueError('Unknown helper role.')
    for name in ROLE_MODELS[role]:
        if name not in installed:
            continue
        task = 'vision' if role == 'vision' else 'coding' if name.startswith('qwen2.5-coder:') else 'general'
        selected = select_model({name}, hardware, resident_model_gib, task=task)
        if selected is not None:
            return selected
    # Preserve an installed generic fallback, but never send an image to a text-only model.
    return None if role == 'vision' else select_model(installed, hardware, resident_model_gib)


def routing_record(prompt, installed, hardware, task='general', resident_model_gib=0):
    role = infer_role(prompt, task)
    profile = choose_helper(installed, hardware, role, resident_model_gib)
    return role, profile, {
        'role': role, 'model': profile.model if profile else None, 'automatic': True,
        'reason': ('Installed helper fits this role and the conservative memory budget.' if profile else
                   'No configured helper fits available memory; use offline evidence or exact calculation.'),
        'parallel_models': 1, 'accuracy_benchmark': 'Not established by routing; verify important outputs.',
    }
