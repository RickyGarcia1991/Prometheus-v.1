"""Route explicit intent without treating ordinary homonyms as source code."""
import pytest

from prometheus_assistant.model_routing import infer_role


@pytest.mark.parametrize('prompt,role', [
    ('What is the function of a capacitor?', 'general'),
    ('Explain the function of the kidneys.', 'general'),
    ('How do I remove rust from a bicycle chain?', 'general'),
    ('What causes rust on metal?', 'general'),
    ('Tell me about Java coffee.', 'general'),
    ('When should I travel to Java island?', 'general'),
    ('What is the habitat of a python snake?', 'general'),
    ('What is the postal code for this address?', 'general'),
    ('Explain the dress code for dinner.', 'general'),
    ('Write a script for a short film.', 'writing'),
    ('Draft a script for a podcast.', 'writing'),
    ('Translate this Python error into Spanish.', 'translation'),
    ('Translate the phrase "Rust programming" into French.', 'translation'),
    ('Translate the phrase "to Python" into French.', 'translation'),
    ('Translate this Java documentation into German.', 'translation'),
    ('Traduce este error de Python al español.', 'translation'),
])
def test_ordinary_language_and_translation_do_not_switch_to_coding(prompt, role):
    assert infer_role(prompt) == role


@pytest.mark.parametrize('prompt', [
    'Write a Python function to subtract two numbers.',
    'Explain the function of Python enumerate.',
    'Explain Rust ownership and borrowing.',
    'Explain Rust ownership using a bicycle chain analogy.',
    'Remove the Rust compiler warning from this code.',
    'Write Java code to classify coffee drinks.',
    'Explain Java class syntax using a coffee example.',
    'Debug the Python class named Snake.',
    'Write Python code to convert Morse code into text.',
    'Write code to zip these files.',
    'Write code that validates a postal code.',
    'Write a function that returns a function of its input.',
    'Write a Python script to edit this film.',
    'Run this script before recording the podcast.',
    'Write a Python function to translate French text into Spanish.',
    'Translate this Python function into JavaScript.',
    'Translate this Java code to Python.',
    'Translate this pseudocode to C++.',
    'Review this SQL query.',
])
def test_clear_programming_intent_still_uses_coding(prompt):
    assert infer_role(prompt) == 'coding'


def test_explicit_task_and_image_keep_authoritative_precedence():
    assert infer_role('Translate this Python error into Spanish.', task='coding') == 'coding'
    assert infer_role('What is the function of a capacitor?', task='coding') == 'coding'
    assert infer_role('Translate this Python error into Spanish.', image=True) == 'vision'
