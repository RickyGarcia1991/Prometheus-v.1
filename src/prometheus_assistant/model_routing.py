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


def candidate_names(role):
    """Role preferences followed by known text fallbacks, never text for images.

    Apply runtime/quality exclusions to this complete list before selecting a
    fitting profile, so a fallback cannot reintroduce a paused helper.
    """
    if role not in ROLE_MODELS:
        raise ValueError('Unknown helper role.')
    names = ROLE_MODELS[role]
    if role != 'vision':
        names += ROLE_MODELS['general']
    return tuple(dict.fromkeys(names))


def infer_role(prompt, task='general', *, image=False):
    if image:
        return 'vision'
    if task == 'coding':
        return 'coding'
    text = prompt.lower()
    words = set(re.findall(r'[a-z+#]+', text))
    # Translating technical prose is a language task. Porting source to another
    # programming language remains coding; a quoted language name alone does
    # not override an explicit translation request.
    translation = re.match(r'\s*(?:(?:please|can you|could you|would you)\s+)?'
                           r'(?:translate|traducir|traduce)\b', text)
    destinations = re.findall(r'\b(?:to|into)\s+([a-z+#]+)\b', text)
    code_destination = bool(translation and destinations and destinations[-1] in {
        'python', 'javascript', 'typescript', 'java', 'rust', 'go', 'sql',
        'c', 'c++', 'c#', 'bash', 'powershell', 'html', 'css'})
    if translation and not code_destination:
        return 'translation'
    coding_words = words.copy()
    # Remove only the ambiguous cue. Other programming terms still win, as in
    # "Explain the function of Python enumerate" or "debug a class named Snake".
    if not re.search(r'\bfunction\b', re.sub(r'\bfunction\s+of\b', '', text)):
        coding_words.discard('function')
    programming_context = bool(words & {'programming', 'coding', 'debug', 'debugging',
        'compiler', 'compile', 'refactor', 'class', 'syntax', 'variable', 'method',
        'library', 'package', 'ownership', 'borrowing', 'cargo', 'maven', 'pip'})
    if not programming_context and words & {'corrosion', 'metal', 'bicycle', 'chain', 'oxidation'}:
        coding_words.discard('rust')
    if not programming_context and words & {'coffee', 'island', 'indonesia'}:
        coding_words.discard('java')
    if not programming_context and words & {'snake', 'snakes', 'reptile', 'habitat', 'venom', 'pet'}:
        coding_words.discard('python')
    if (not programming_context and not words & {'run', 'execute'}
            and words & {'film', 'movie', 'play', 'podcast', 'screenplay', 'dialogue'}):
        coding_words.discard('script')
    if not re.search(r'\bcode\b', re.sub(
            r'\b(?:postal|zip|dress|morse|country|area|building)\s+code\b|\bcode\s+of\s+conduct\b', '', text)):
        coding_words.discard('code')
    if code_destination or coding_words & {'code', 'coding', 'debug', 'debugging', 'function', 'programming', 'python',
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
    names = ROLE_MODELS[role]
    # A 7 GiB Surface must retain the UI's 2 GiB system reserve while decoding
    # image tokens. Prefer the compact native vision helper on sub-8 GiB hosts.
    if role == 'vision' and hardware.ram_gib < 8:
        names = ('qwen3.5:0.8b', 'qwen3-vl:2b')
    for name in names:
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
