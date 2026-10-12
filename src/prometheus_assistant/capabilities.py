"""Explicit read tools shared by the interface and capability discovery.

Selection comes from the user's mode and source control, never generated text.
This is a local tool catalog, not an MCP transport implementation.
"""
from pathlib import Path
import math
import re
from .tool_registry import ArgSpec, ToolSpec, ToolRegistry
from .evidence_text import focused_terms, plural_variants

SOURCES = {'documents', 'project', 'offline'}


def evidence_registry(app):
    def documents(request):
        from .document_library import DocumentLibrary
        root = app.data_root/'Library'
        if not (root/'documents.sqlite3').is_file():
            return {'results': [], 'retrieval': 'keyword', 'note': 'Import files in the Documents workbench first.'}
        # Retrieval never starts a second model or refreshes a source online.
        return DocumentLibrary(root, read_only=True).search(request.arguments['query'], limit=20, semantic=False)

    def project(request):
        from .documents import search_documents
        return search_documents(Path(request.arguments['project']), request.arguments['query'], include_code=True)

    def offline(request):
        from .ui_server import reference_query
        return reference_query(request.arguments['query'], request.arguments['profile'])

    query = ArgSpec(max_length=4000)
    return ToolRegistry([
        ToolSpec('evidence', 'documents', 'Search latest imported document versions with source locations.', documents, {'query': query}),
        ToolSpec('evidence', 'project', 'Search a user-selected project, excluding known private files and links.', project,
                 {'query': query, 'project': ArgSpec(max_length=500)}),
        ToolSpec('evidence', 'offline', 'Search a selected installed offline reference collection.', offline,
                 {'query': query, 'profile': ArgSpec(max_length=30)}),
    ])


def _source_excerpt(row):
    context = row.get('context') if not row.get('context_truncated') else None
    return context or row.get('excerpt') or row.get('summary') or row.get('snippet') or row.get('text') or row.get('description')


def _diverse_passages(rows, query):
    """Keep coherent source text, rewarding additional requested subjects.

    The original keyword ranking supplies the first passage. Later choices
    balance marginal query coverage, rank and document diversity. A document
    may supply several distinct sections; no source text is synthesized.
    """
    wanted = set(focused_terms(query))
    variants = {term: set(plural_variants(term, query)) for term in wanted}

    def hits(words):
        return {term for term, forms in variants.items() if forms & words}

    candidates = []
    for rank, row in enumerate(rows[:20]):
        if not isinstance(row, dict):
            continue
        excerpt = _source_excerpt(row)
        if not isinstance(excerpt, str) or not excerpt.strip():
            continue
        words = re.findall(r'[^\W_]+', excerpt[:4000].casefold())
        body_hits = hits(set(words))
        title_hits = hits(set(re.findall(r'[^\W_]+', str(row.get('title', ''))[:240].casefold())))
        # A short heading or definition label provides a conservative signal
        # that a term is the passage's subject, rather than incidental prose.
        labels = re.findall(r'^\s*(?:#{1,6}\s+([^\n]{1,100})|(?:[-*]\s+)?([^:\n]{1,100}):)',
                            excerpt[:4000], re.M)
        subjects = hits(set(re.findall(r'[^\W_]+', ' '.join(a or b for a,b in labels).casefold())))
        shingles = {tuple(words[i:i+5]) for i in range(max(0, len(words)-4))}
        identity = row.get('document_id') or (row.get('title'), row.get('sha256'), row.get('source_url'))
        candidates.append(dict(row=row, rank=rank, hits=body_hits | title_hits, body_hits=body_hits, subjects=subjects,
                               shingles=shingles, text=' '.join(words), identity=identity))
    weights = {term: 1/math.sqrt(sum(term in item['hits'] for item in candidates))
               for term in wanted if any(term in item['hits'] for item in candidates)}
    total_weight = sum(weights.values()) or 1
    selected, covered, body_covered, documents = [], set(), set(), set()
    while candidates and len(selected)<5:
        eligible = []
        for item in candidates:
            duplicate = False
            for previous in selected:
                if item['text'] == previous['text']:
                    duplicate = True
                    break
                smaller = min(len(item['shingles']), len(previous['shingles']))
                if (smaller >= 4 and len(item['shingles'] & previous['shingles'])/smaller >= .8
                        and not item['body_hits']-covered):
                    duplicate = True
                    break
            if not duplicate:
                eligible.append(item)
        if not eligible:
            break
        if not selected:
            chosen = eligible[0]
        else:
            def priority(item):
                marginal = sum(weights[t] for t in item['hits']-covered)/total_weight
                relevance = sum(weights[t] for t in item['hits'])/total_weight
                diversity = .005 if item['hits'] and item['identity'] not in documents else 0
                # Keyword rank remains the relevance anchor. Unbounded novelty
                # can promote an unrelated passage merely for generic words
                # such as "new", "plan" or "steps". Bound all bonuses to .03;
                # they cannot displace a distinct top-five keyword candidate.
                return (1/(item['rank']+1) + .02*marginal + .005*relevance + diversity, -item['rank'])
            chosen = max(eligible, key=priority)
            if len(selected) == 4 and not chosen['body_hits']-body_covered:
                # Reserve at most the final slot for one nearby, otherwise
                # missing subject. Shared context and a section label prevent
                # rare generic prose from defeating the relevance ranking.
                nearby = [item for item in eligible
                          if item['rank'] <= chosen['rank']+3
                          and item['subjects']-body_covered
                          and len(item['body_hits']) >= 3
                          and len(item['body_hits'] & body_covered) >= 2]
                if nearby:
                    chosen = min(nearby, key=lambda item: item['rank'])
        selected.append(chosen)
        covered.update(chosen['hits'])
        body_covered.update(chosen['body_hits'])
        documents.add(chosen['identity'])
        candidates.remove(chosen)
    return [item['row'] for item in selected]


def retrieve(app, request):
    from .application_policy import require_dispatch
    require_dispatch('evidence.retrieve')
    from .application_policy import require_dispatch
    require_dispatch('evidence.retrieve')
    source = request.get('source', 'documents')
    if source not in SOURCES:
        raise ValueError('Choose documents, a project, or offline references.')
    arguments = {'query': request['prompt']}
    if source == 'project':
        arguments['project'] = request.get('project', '')
    if source == 'offline':
        arguments['profile'] = request.get('profile', 'public')
    tool = evidence_registry(app).get('evidence', source)
    data = tool.executor(tool.request('Retrieve the selected source evidence.', arguments))
    rows = data.get('results', data.get('sources', data.get('articles', [])))
    if source == 'documents':
        rows = _diverse_passages(rows, request['prompt'])
    sources = []
    for row in rows[:5]:
        if not isinstance(row, dict):
            continue
        context = row.get('context') if not row.get('context_truncated') else None
        excerpt = _source_excerpt(row)
        if not isinstance(excerpt, str) or not excerpt.strip():
            continue
        title = str(row.get('title') or row.get('source') or row.get('path') or 'Reference')[:240]
        location = str(row.get('citation') or row.get('source_url') or row.get('url') or row.get('path') or '')
        # URLs are identifiers, not display text. A shortened URL can point at a
        # different revision or resource; preserve supported links verbatim.
        if len(location) > 2000:
            raise ValueError('Source citation exceeds the supported 2,000-character limit.')
        if row.get('location'):
            location = (location+' | ' if location else '')+str(row['location'])[:160]
        if row.get('line'):
            location += ' | match line '+str(row['line'])
        if context and row.get('context_start_line'):
            location += f" | context lines {row['context_start_line']}-{row['context_end_line']}"
        sources.append({'id': 'S'+str(len(sources)+1), 'title': title, 'location': location,
                        'excerpt': excerpt[:4000], 'excerpt_truncated': len(excerpt)>4000 or bool(row.get('context_truncated')),
                        **{k: row[k] for k in ('version', 'version_id', 'sha256', 'source_date', 'imported_at', 'is_latest') if k in row}})
    return {'source': source, 'sources': sources, 'retrieval': data.get('retrieval', 'bounded text search'),
            'note': data.get('note', ''), 'evidence_is_untrusted_data': True, 'generated_answer': False}


def excerpt_reply(data, reason):
    rows = data['sources']
    if not rows:
        return 'No matching evidence was found in the selected source. '+data.get('note', '')
    return reason+'\n\n'+ '\n\n'.join(f"[{r['id']}] {r['title']}\n{r['excerpt']}\n{r['location']}" for r in rows)


def catalog(app):
    # Conditions describe supported workflows, not a claim that every installed
    # model, compiler, microphone or connected service passed a live test.
    rows = [
        ('ask', 'Ask with evidence', 'Choose imported documents, a project folder, or an offline collection. One local inference when memory permits; cited excerpts otherwise.', False, False),
        ('chat', 'Conversation and automatic helpers', 'Requires an installed local model and enough available RAM. Small benchmark checks guide selection, not general accuracy guarantees.', False, False),
        ('vision', 'Ask about an image', 'Select a PNG/JPEG up to 8 MiB. Requires a local vision model and enough memory. Image bytes are not retained.', False, False),
        ('documents', 'Versioned document library', 'Import TXT, Markdown, CSV, text PDF or DOCX; keyword search needs no model. Meaning indexing is an explicit action.', True, False),
        ('coding', 'Project editing and checks', 'Select a development project; preview and apply changes with undo. Running project checks executes its code.', True, False),
        ('voice', 'Local dictation and speech', 'Dictation needs installed whisper.cpp, a speech model and a working microphone. Playback needs an installed local browser voice.', False, False),
        ('math', 'Exact mathematics', 'Bounded rational arithmetic is available without a model; symbolic helpers are also exposed through the CLI tool registry.', False, False),
        ('research', 'Public research and saved versions', 'Explicit online search sends the query to the selected publisher. Source watches must be enabled individually.', True, True),
        ('remote', 'Connected computers', 'Requires a configured connection, identity review and specific action authorization.', True, True),
        ('home', 'Smart home', 'Requires a configured Home Assistant hub, named devices and the control key.', True, True),
        ('maintenance', 'Health, backup and recovery', 'Checks and backups run on request. Signed release activation requires the existing signing identity.', True, False),
    ]
    capabilities = [{'id': ident, 'title': title, 'availability': 'conditional' if ident != 'math' else 'ready',
                     'conditions': condition, 'may_change_state': writes, 'may_use_network': network}
                    for ident, title, condition, writes, network in rows]
    return {'schema_version': 1, 'capabilities': capabilities,
            'evidence_tools': evidence_registry(app).structured_catalog(),
            'dispatch': 'Explicit user selection; generated text cannot invoke these tools.',
            'scope': 'Application capabilities; proprietary model internals and account access are not transferred.'}
