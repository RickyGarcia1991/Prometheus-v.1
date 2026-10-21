"""Bounded local text search with literal evidence and line citations."""
from pathlib import Path
import re

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


def search_documents(root, query, limit=5, *, include_code=False):
    root = Path(root).resolve()
    if not root.is_dir():
        raise ValueError('Choose an existing document directory.')
    terms = set(re.findall(r'\w+', query.casefold()))
    if not terms or len(query) > 4000:
        raise ValueError('Enter a search query of 1–4000 characters.')
    results, skipped, total, count = [], [], 0, 0
    extensions = SUPPORTED | CODE_SUPPORTED if include_code else SUPPORTED
    # Do not traverse directory links or junctions outside the selected folder.
    import os
    for folder, directories, files in os.walk(root, followlinks=False):
        directories[:] = sorted(d for d in directories if not d.startswith('.')
                                and (not include_code or d not in {'node_modules', '__pycache__', 'venv', 'vendor', 'target', 'dist', 'build'})
                                and not Path(folder, d).is_symlink()
                                and not getattr(Path(folder, d), 'is_junction', lambda: False)())
        for name in sorted(files):
            path = Path(folder, name)
            if ((path.suffix.lower() not in extensions and not (include_code and name in {'Dockerfile','Makefile','CMakeLists.txt'}))
                    or path.is_symlink() or name.startswith('.')):
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
            for number, line in enumerate(content.splitlines(), 1):
                score = len(terms & set(re.findall(r'\w+', line.casefold())))
                if score:
                    results.append({'source': path.relative_to(root).as_posix(), 'line': number,
                                    'excerpt': line[:600], 'matched_terms': score})
    results.sort(key=lambda r: (-r['matched_terms'], r['source'], r['line']))
    return {'results': results[:limit], 'skipped_files': skipped,
            'files_examined': count, 'generated_answer': False}
