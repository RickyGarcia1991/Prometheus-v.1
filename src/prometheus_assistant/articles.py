"""Bounded offline retrieval from the installed Wikimedia ZIM snapshots."""
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import quote
import re
from .offline_archive import ARCHIVES, archive_root

MAX_ARTICLE_BYTES = 5 * 1024 * 1024
MAX_QUERY_CHARS = 256

class ArticleError(ValueError):
    pass

class PlainText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.hidden = 0
    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style", "noscript"}: self.hidden += 1
        if tag in {"p", "div", "br", "li", "h1", "h2", "h3", "tr"}: self.parts.append(" ")
    def handle_endtag(self, tag):
        if tag in {"script", "style", "noscript"} and self.hidden: self.hidden -= 1
        if tag in {"p", "div", "li", "h1", "h2", "h3"}: self.parts.append(" ")
    def handle_data(self, data):
        if not self.hidden: self.parts.append(data)

def plain_text(html):
    parser = PlainText(); parser.feed(html); parser.close()
    return re.sub(r"\s+", " ", "".join(parser.parts)).strip()

def _bindings():
    try:
        from libzim.reader import Archive
        from libzim.search import Query, Searcher
        return Archive, Query, Searcher
    except ImportError as error:
        raise ArticleError("Offline ZIM reader is missing. Install the pinned offline-reader dependency into this Python runtime.") from error

def _selected(root, project, query=""):
    if not root or not Path(root).is_dir():
        raise ArticleError("Set PROMETHEUS_RESOURCE_ROOT to the connected resource directory.")
    choices = [a for a in ARCHIVES if project in {"auto", "all", a.project, a.id}]
    if not choices: raise ArticleError("Unknown archive. Use the resources command to list collection IDs.")
    base = archive_root(root).resolve()
    selected=[]
    for item in choices:
        path=base/item.project/item.filename
        if not path.resolve().is_relative_to(base): raise ArticleError("Archive path escapes the library.")
        if any(p.is_symlink() or getattr(p,'is_junction',lambda:False)() for p in (path,path.parent)):
            raise ArticleError("Archive links and junctions are not accepted.")
        selected.append((item,path))
    if project=="auto":
        words=set(re.findall(r'\w+',query.casefold()))
        # Ordinary questions seldom name a catalog subject. Expand only known
        # topic words; this affects archive selection, not the user's query.
        topic_terms={
            'math':{'quadratic','equation','equations','fraction','fractions','polynomial','polynomials','derivative','derivatives','integral','integrals','trigonometry','theorem','proof','matrices'},
            'algebra':{'quadratic','polynomial','polynomials','linear','equation','equations'},
            'statistics':{'probability','regression','variance','deviation','hypothesis'},
            'physics':{'velocity','acceleration','momentum','gravity','quantum','electromagnetic'},
            'chemistry':{'molecule','molecules','reaction','periodic','stoichiometry'},
            'biology':{'photosynthesis','genetics','organism','organisms','evolution'},
            'medicine':{'diabetes','anatomy','symptom','symptoms','disease'},
            'history':{'historical','civilization','medieval','renaissance'},
        }
        words.update(subject for subject,terms in topic_terms.items() if words & terms)
        def score(pair):
            item,_=pair
            tags=set(item.subjects)
            if item.project=='wiktionary':tags.update(('word','meaning','dictionary','definition','etymology','translate'))
            if item.project=='wikisource':tags.update(('historical','history','poem','literature','manuscript'))
            return len(words & tags)
        installed=[pair for pair in selected if pair[1].is_file()]
        relevant=sorted((pair for pair in installed if score(pair)),key=lambda p:-score(p))
        broad_order={'wikipedia':0,'wikibooks':1,'wiktionary':2,'wikisource':3}
        fallback=sorted(installed,key=lambda p:(broad_order.get(p[0].project,4),p[0].id))
        selected=(relevant+[pair for pair in fallback if pair not in relevant])[:4]
    return selected

def _entry(archive, item, path, chars, query=""):
    entry = archive.get_entry_by_path(path)
    for _ in range(8):
        if not entry.is_redirect: break
        entry = entry.get_redirect_entry()
    else: raise ArticleError("Archive redirect chain exceeded the limit.")
    blob = entry.get_item()
    if blob.size > MAX_ARTICLE_BYTES:
        raise ArticleError("Article exceeds the 5 MiB read limit.")
    if not blob.mimetype.startswith(("text/html", "text/plain", "application/xhtml")):
        raise ArticleError("Selected entry is not a text article.")
    text = plain_text(bytes(blob.content).decode("utf-8", errors="replace"))
    offset = 0
    if query:
        terms = re.findall(r"\w+", query.lower())
        positions = [text.lower().find(t) for t in terms if len(t) > 2]
        positions = [p for p in positions if p >= 0]
        if positions: offset = max(0, min(positions) - 120)
    excerpt = text[offset:offset + chars]
    path = entry.path
    title_path = path[2:] if path.startswith("A/") else path
    return {"archive": item.project, "archive_id": item.id, "archive_version": item.version,
            "archive_sha256": item.sha256, "path": path, "title": entry.title,
            "text": excerpt, "truncated": offset > 0 or len(text) > chars,
            "citation": f"zim://{item.id}/{quote(path, safe='/')}@{item.version}",
            "source_url": item.reference_url or f"https://en.{item.project}.org/wiki/{quote(title_path, safe='/')}",
            "source_url_scope": "collection" if item.reference_url else "article",
            "license_note": item.license_note}

def search_articles(root, query, project="auto", limit=5):
    query = query.strip()
    if not query or len(query) > MAX_QUERY_CHARS:
        raise ArticleError("Article queries must contain 1–256 characters.")
    if not 1 <= limit <= 10: raise ArticleError("Result limit must be between 1 and 10.")
    Archive, Query, Searcher = _bindings()
    groups = []; warnings = []; searched = []
    for item, path in _selected(root, project, query):
        if not path.is_file():
            warnings.append({"archive": item.project, "error": "Archive is not installed."}); continue
        try:
            archive = Archive(str(path))
            found = []
            try: found.append(archive.get_entry_by_title(query).path)
            except KeyError: pass
            if archive.has_fulltext_index:
                searcher = Searcher(archive)
                search = searcher.search(Query().set_query(query))
                found.extend(list(search.getResults(0, min(limit, 5))))
            elif not found:
                warnings.append({"archive": item.project, "error": "No full-text index; exact title lookup only."})
            rows = []
            for entry_path in dict.fromkeys(found):
                try: rows.append(_entry(archive, item, entry_path, 1000, query))
                except (RuntimeError, KeyError, ArticleError) as error:
                    warnings.append({"archive": item.project, "error": str(error)})
                if len(rows) >= min(limit, 5): break
            groups.append(rows); searched.append(item.id)
        except (RuntimeError, OSError, ValueError) as error:
            warnings.append({"archive": item.project, "error": str(error)})
    results = []
    for index in range(max((len(g) for g in groups), default=0)):
        for group in groups:
            if index < len(group): results.append(group[index])
    if not searched: raise ArticleError("No archive could be searched: " + "; ".join(w["error"] for w in warnings))
    return {"query": query, "offline": True, "archives_searched": searched,
            "selection": project, "search_is_exhaustive": project=="all",
            "results": results[:limit], "warnings": warnings}

def read_article(root, project, path, max_chars=12000):
    if not path or len(path) > 2048: raise ArticleError("A valid archive entry path is required.")
    if not 1 <= max_chars <= 50000: raise ArticleError("Article text limit must be between 1 and 50000.")
    selected = _selected(root, project)
    if len(selected) != 1: raise ArticleError("Choose one archive for reading.")
    item, archive_path = selected[0]
    if not archive_path.is_file(): raise ArticleError("Selected archive is not installed.")
    Archive, _, _ = _bindings()
    try: return _entry(Archive(str(archive_path)), item, path, max_chars)
    except (KeyError, RuntimeError, OSError) as error:
        raise ArticleError(f"Unable to read archive entry: {error}") from error
