"""Bounded local text search with literal evidence and line citations."""
from pathlib import Path
import re
from .coding_workspace import _no_links, _private

SUPPORTED = {'.md', '.txt', '.csv'}
CODE_SUPPORTED = {'.py', '.pyi', '.js', '.jsx', '.ts', '.tsx', '.html', '.css', '.scss',
                  '.sql', '.c', '.h', '.cpp', '.hpp', '.cc', '.cs', '.java', '.kt', '.swift',
                  '.go', '.rs', '.rb', '.php', '.r', '.jl', '.m', '.dart', '.lua', '.pl',
                  '.sh', '.ps1', '.fs', '.hs', '.ex', '.exs', '.erl', '.scala', '.clj',
                  '.ml', '.zig', '.asm', '.s', '.f90', '.f', '.cob', '.sol', '.vhd',
                  '.v', '.sv', '.ino', '.yaml', '.yml', '.json', '.toml', '.tf', '.graphql', '.proto'}
MAX_FILE_BYTES = 1_000_000
MAX_FILES = 500
MAX_TOTAL_BYTES = 20_000_000
_STOP = frozenset('a an the and or to of for in on at with please can could would '
                  'you me my our show find search where what how is are was were '
                  'this that it its file files project source code defined'.split())
_TOKENS = re.compile(r'\w+(?:\+\+|#)?', re.UNICODE)


def private_search_name(name):
    """Known credential containers are excluded before any content is opened."""
    return _private(name) or bool(re.fullmatch(
        r'(?:credentials|secrets?|id_rsa|id_dsa|id_ecdsa|id_ed25519|authorized_keys)(?:\..*)?',
        name.casefold()))


def search_query(query):
    if not isinstance(query, str) or not query.strip() or len(query) > 4000:
        raise ValueError('Enter a search query of 1–4000 characters.')
    raw = _TOKENS.findall(query)
    # Preserve an explicitly searched single identifier, including e.g. "find".
    words = [word for word in raw if word.casefold() not in _STOP] if len(raw) > 1 else raw
    if not words:
        raise ValueError('The query needs a meaningful search term or code symbol.')
    terms = tuple(dict.fromkeys(word.casefold() for word in words))
    symbols = {word.casefold() for word in words if '_' in word or
               re.search(r'[a-z][A-Z]', word) or word.endswith(('++', '#'))}
    return terms, symbols


def ranked_lines(content, query_spec, *, excerpt_limit=600, context=False):
    """Keep the match citation separate from adjacent, potentially clipped context."""
    terms, symbols = query_spec
    wanted = set(terms)
    phrase = ' '.join(terms)
    lines = content.splitlines()
    for index, line in enumerate(lines):
        tokens = _TOKENS.findall(line.casefold())
        overlap = wanted & set(tokens)
        if not overlap:
            continue
        phrase_match = len(terms) > 1 and (' '+phrase+' ') in (' '+' '.join(tokens)+' ')
        row = {'line': index + 1, 'excerpt': line[:excerpt_limit], 'matched_terms': len(overlap),
               'rank_score': len(overlap) + 3 * len(symbols & overlap) + 4 * int(phrase_match)}
        if context:
            start, end = max(0, index - 1), min(len(lines), index + 2)
            fragments = [item[:399] for item in lines[start:end]]
            row.update(context='\n'.join(fragments), context_start_line=start + 1,
                       context_end_line=end, context_truncated=any(len(item) > 399 for item in lines[start:end]))
        yield row


def _safe_search_entry(path):
    try:
        _no_links(path)
        return True
    except (OSError, ValueError):
        return False


def search_documents(root, query, limit=5, *, include_code=False):
    root = Path(root).expanduser().absolute()
    _no_links(root)
    if any(private_search_name(part) for part in root.parts):
        raise ValueError('Choose a directory outside private metadata and credentials.')
    root = root.resolve()
    if not root.is_dir():
        raise ValueError('Choose an existing document directory.')
    if type(limit) is not int or not 1 <= limit <= 20:
        raise ValueError('Search result limit must be 1–20.')
    query_spec = search_query(query)
    results, skipped, total, count = [], [], 0, 0
    extensions = SUPPORTED | CODE_SUPPORTED if include_code else SUPPORTED
    # Do not traverse directory links or junctions outside the selected folder.
    import os
    for folder, directories, files in os.walk(root, followlinks=False):
        directories[:] = sorted(d for d in directories if not d.startswith('.') and not private_search_name(d)
                                and (not include_code or d not in {'node_modules', '__pycache__', 'venv', 'vendor', 'target', 'dist', 'build'})
                                and _safe_search_entry(Path(folder, d)))
        for name in sorted(files):
            path = Path(folder, name)
            if ((path.suffix.lower() not in extensions and not (include_code and name in {'Dockerfile','Makefile','CMakeLists.txt'}))
                    or private_search_name(name) or name.startswith('.') or not _safe_search_entry(path)):
                continue
            if not path.resolve().is_relative_to(root):
                continue
            count += 1
            if count > MAX_FILES:
                raise ValueError('More than 500 text files; select a smaller directory.')
            try:
                if path.stat().st_size > MAX_FILE_BYTES:
                    skipped.append(path.relative_to(root).as_posix())
                    continue
                with path.open('rb') as f:
                    data = f.read(MAX_FILE_BYTES + 1)
                total += len(data)
                if total > MAX_TOTAL_BYTES:
                    raise ValueError('Document collection exceeds 20 MB; select a smaller directory.')
                if len(data) > MAX_FILE_BYTES:
                    skipped.append(path.relative_to(root).as_posix())
                    continue
                content = data.decode('utf-8-sig')
            except (OSError, UnicodeError):
                skipped.append(path.relative_to(root).as_posix())
                continue
            for row in ranked_lines(content, query_spec, context=include_code):
                results.append({'source': path.relative_to(root).as_posix(), **row})
                if len(results) > limit * 2:
                    results.sort(key=lambda r: (-r['rank_score'], r['source'], r['line']))
                    del results[limit:]
    results.sort(key=lambda r: (-r['rank_score'], r['source'], r['line']))
    if not include_code:
        # Preserve the original document-search result schema for existing callers.
        for row in results:
            row.pop('rank_score')
    return {'results': results[:limit], 'skipped_files': skipped,
            'files_examined': count, 'generated_answer': False}
