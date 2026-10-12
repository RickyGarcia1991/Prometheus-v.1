"""Neutral integration fixtures for bounded, diverse Documents evidence."""
from types import SimpleNamespace

from prometheus_assistant.capabilities import retrieve
from prometheus_assistant.document_library import DocumentLibrary


def app_with_library(tmp_path):
    return SimpleNamespace(data_root=tmp_path), DocumentLibrary(tmp_path / 'Library')


def ask(app, prompt):
    return retrieve(app, {'source': 'documents', 'prompt': prompt})


def test_repeated_overview_cannot_crowd_out_other_requested_subjects(tmp_path):
    app, library = app_with_library(tmp_path)
    repeated = ('The beacon controller installation needs a power plan and a mounting plan.\n' * 30)
    library.import_bytes('beacon-controller.md', repeated.encode())
    library.import_bytes('power-planning.md',
        b'Power: the beacon requires a documented continuous and startup current budget.')
    library.import_bytes('mounting-planning.md',
        b'Mounting: record the beacon enclosure dimensions and the required connector clearance.')
    result = ask(app, 'Plan beacon controller installation: power current budget and mounting dimensions clearance.')
    titles = [row['title'] for row in result['sources']]
    assert 'power-planning.md' in titles
    assert 'mounting-planning.md' in titles
    assert titles.count('beacon-controller.md') <= 1
    assert len(titles) <= 5


def test_new_topic_beyond_first_five_search_hits_can_be_selected(tmp_path, monkeypatch):
    app, library = app_with_library(tmp_path)
    # Use a controlled retrieval ranking while retaining the supported read-only
    # library boundary and genuine source records returned by SQLite.
    repeated = []
    for index in range(6):
        row = library.import_bytes(f'overview-{index}.md',
            b'Controller installation power supply and enclosure overview.')
        repeated.append({**row, 'excerpt': 'Controller installation power supply and enclosure overview.',
                         'location': 'text, characters 1-64'})
    special = library.import_bytes('network.md', b'Network isolation requires the dedicated optical adapter.')
    special = {**special, 'excerpt': 'Network isolation requires the dedicated optical adapter.',
               'location': 'text, characters 1-62'}
    calls = []

    def search(self, query, *, limit, semantic):
        calls.append((limit, semantic, self.read_only))
        return {'results': [*repeated, special][:limit], 'retrieval': 'keyword'}

    monkeypatch.setattr(DocumentLibrary, 'search', search)
    result = ask(app, 'Controller installation power supply enclosure and network isolation adapter requirements.')
    assert 'network.md' in [row['title'] for row in result['sources']]
    assert calls == [(20, False, True)]
    source = next(row for row in result['sources'] if row['title'] == 'network.md')
    assert source['excerpt'] == special['excerpt']
    assert source['sha256'] == special['sha256']
    assert source['version_id'] == special['version_id']


def test_distinct_same_document_sections_and_fresh_followup_are_kept(tmp_path):
    app, library = app_with_library(tmp_path)
    connector = 'Orion Mk2 connector J7: pin 1 is VBUS; pin 2 is RETURN. Pin 1 is nearest the blue edge.\n'
    battery = 'Orion Mk2 backup storage: use its documented rechargeable cell; primary cells are unsuitable because this board charges the cell.\n'
    library.import_bytes('orion-mk2.md', (connector + 'Mechanical notes.\n' * 100 + battery).encode(),
                         source_url='https://example.org/orion/mk2/revision-7')
    library.import_bytes('orion-mk1.md', b'Orion Mk1 connector J7 pin 1 is RETURN; pin 2 is VBUS.')
    first = ask(app, 'For Orion Mk2, give the connector J7 pins and the backup storage cell requirement.')
    excerpts = [row['excerpt'] for row in first['sources'] if row['title'] == 'orion-mk2.md']
    assert any('pin 1 is VBUS' in body for body in excerpts)
    assert any('primary cells are unsuitable' in body for body in excerpts)
    followup = ask(app, 'On Orion Mk2, can the backup storage use primary cells when the board charges the cell?')
    assert 'primary cells are unsuitable' in followup['sources'][0]['excerpt']
    assert followup['sources'][0]['location'].startswith('https://example.org/orion/mk2/revision-7')


def test_exact_absent_designator_remains_no_evidence(tmp_path):
    app, library = app_with_library(tmp_path)
    library.import_bytes('general.md', b'The Orion board has resistors and connectors; inspect its revision.')
    result = ask(app, 'What is the exact value of R876 on the Orion board?')
    assert result['sources'] == []
    assert 'R876' in result['note']


def test_rare_generic_words_do_not_promote_an_unrelated_business_plan(tmp_path, monkeypatch):
    app, library = app_with_library(tmp_path)
    texts = [
        ('pump-startup.md', 'Pump motor current and pressure troubleshooting: inspect the load and isolate the fault.'),
        ('pump-supply.md', 'Pump motor supply voltage and current must be measured before pressure or load testing.'),
        ('pump-mechanical.md', 'For a pump motor fault, inspect the coupling and bearing before testing under load.'),
        ('pump-pressure.md', 'Compare pump pressure against the documented curve; verify valve position and leakage.'),
        ('pump-wiring.md', 'Check pump motor winding continuity and connector resistance with the supply disconnected.'),
        ('business-plan.md', 'Propose a new step-by-step business plan. Know whether the sales references support the campaign; report the current board decisions.'),
    ]
    rows = []
    for name, body in texts:
        record = library.import_bytes(name, body.encode())
        rows.append({**record, 'excerpt': body, 'location': 'text', 'keyword_score': -20+len(rows)})
    monkeypatch.setattr(DocumentLibrary, 'search', lambda self, query, **options:
        {'results': rows[:options['limit']], 'retrieval': 'keyword'})
    result = ask(app, 'My new pump motor will not start and pressure and current are low. Propose a step-by-step troubleshooting plan using references; we do not know whether the supply or load is at fault.')
    titles = [source['title'] for source in result['sources']]
    assert titles[:2] == ['pump-startup.md', 'pump-supply.md']
    assert 'business-plan.md' not in titles
    assert len(titles) == 5


def test_nearby_missing_subject_replaces_redundant_final_passage(tmp_path, monkeypatch):
    app, library = app_with_library(tmp_path)
    texts = [
        ('pump-overview.md', 'Pump motor inspection: verify supply, load, pressure and alignment.'),
        ('pump-pressure.md', 'At the pump inlet, measure pressure before starting the motor.'),
        ('pump-coupling.md', 'Pump coupling alignment requires measurement using approved tools.'),
        ('pump-load.md', 'Pump motor load changes must be compared to the recorded operating envelope.'),
        ('pump-condition.md', 'Pump motor pressure measurement varies with the operating conditions.'),
        ('sales.md', 'Business plan: report current sales. Propose a new step-by-step campaign and know whether references help.'),
        ('pump-bearing.md', 'Bearings: inspect pump motor temperature before applying the load.'),
    ]
    rows = []
    for name, body in texts:
        record = library.import_bytes(name, body.encode(), source_url='https://example.org/records/'+name)
        rows.append({**record, 'excerpt': body, 'location': 'text', 'keyword_score': -30+len(rows)})
    monkeypatch.setattr(DocumentLibrary, 'search', lambda self, query, **options:
        {'results': rows[:options['limit']], 'retrieval': 'keyword'})
    result = ask(app, 'For pump motor inspection, propose a new step-by-step plan using my references. '
                      'Check supply, load, pressure, alignment, coupling and bearing inspection.')
    sources = result['sources']
    assert [row['title'] for row in sources[:4]] == [row[0] for row in texts[:4]]
    assert len(sources) == 5
    assert sources[-1]['title'] == 'pump-bearing.md'
    assert sources[-1]['excerpt'] == rows[-1]['excerpt']
    assert sources[-1]['sha256'] == rows[-1]['sha256']
    assert sources[-1]['version_id'] == rows[-1]['version_id']
    assert sources[-1]['location'] == 'https://example.org/records/pump-bearing.md | text'


def test_generic_prose_word_cannot_displace_final_technical_passage(tmp_path, monkeypatch):
    app, library = app_with_library(tmp_path)
    texts = [
        ('pump-overview.md', 'Pump motor inspection: verify supply, load, pressure and alignment.'),
        ('pump-pressure.md', 'At the pump inlet, measure pressure before starting the motor.'),
        ('pump-coupling.md', 'Pump coupling alignment requires measurement using approved tools.'),
        ('pump-load.md', 'Pump motor load changes must be compared to the recorded operating envelope.'),
        ('pump-condition.md', 'Pump motor pressure measurement varies with the operating conditions.'),
        ('attribution.md', 'Publication notes: this documented material mentions pump motor inspections and its publisher.'),
    ]
    rows = []
    for name, body in texts:
        record = library.import_bytes(name, body.encode())
        rows.append({**record, 'excerpt': body, 'location': 'text'})
    monkeypatch.setattr(DocumentLibrary, 'search', lambda self, query, **options:
        {'results': rows[:options['limit']], 'retrieval': 'keyword'})
    result = ask(app, 'For pump motor inspection, explain the documented supply, load, pressure, alignment and coupling checks.')
    assert [row['title'] for row in result['sources']] == [row[0] for row in texts[:5]]
