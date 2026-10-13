import ast
import pytest
from prometheus_assistant.response_checks import finalize_code_only

PROMPT = 'Write only a Python function named difference with arguments a and b that returns a minus b. No Markdown, explanation, comments or tests.'
SOURCE = 'def difference(a, b):\n    return a - b'


def test_requested_source_is_pasteable_and_code_is_preserved():
    final = finalize_code_only(PROMPT, '```python\n' + SOURCE + '\n```')
    assert final == SOURCE + '\n'
    assert ast.dump(ast.parse(final)) == ast.dump(ast.parse(SOURCE))


@pytest.mark.parametrize('prompt', [
    'Output only Python source for a function named square with one argument x, returning x multiplied by x. No Markdown or commentary.',
    'Return only code. Without code fences.',
])
def test_explicit_variants(prompt):
    assert finalize_code_only(prompt, '```python\ndef square(x):\n    return x * x\n```') == 'def square(x):\n    return x * x\n'


@pytest.mark.parametrize('prompt', [
    'Explain how this function works.',
    'Write a Python function and explain it.',
    'Write only a Python function. Use Markdown.',
    'Critique this instruction: "' + PROMPT + '"',
    '> ' + PROMPT,
    '```text\n' + PROMPT + '\n```',
    "Explain why this quoted instruction is strict: 'Be concise. Write only Python code. No Markdown.' Keep a fenced Python example.",
    'Discuss `Be concise. Write only Python code. No Markdown.` and give a fenced example.',
])
def test_other_requests_preserve_presentation(prompt):
    reply = '```python\n' + SOURCE + '\n```'
    assert finalize_code_only(prompt, reply) == reply


@pytest.mark.parametrize('reply', [
    SOURCE,
    'Here is the function:\n```python\n' + SOURCE + '\n```',
    '```python\n' + SOURCE + '\n```\nExplanation follows.',
    '```python\nx=1\n```\n```python\ny=2\n```',
    '```python\n```',
    '```python\n' + SOURCE,
])
def test_unexpected_output_is_not_silently_rewritten(reply):
    assert finalize_code_only(PROMPT, reply) == reply
