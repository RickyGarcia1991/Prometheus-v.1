"""Remove conversational filler while retaining quoted literal FTS terms."""
import re
STOPWORDS=frozenset('a an the how what where when why can could do does did i you we is are was were to of for in on with please me my about use using explain show find'.split())

def query_terms(query):
    words=re.findall(r'[^\W_]+',query,re.UNICODE)
    if not words or len(words)>24:raise ValueError('Use 1–24 search words.')
    terms=list(dict.fromkeys(w for w in words if w.casefold() not in STOPWORDS))
    if not terms or len(terms)>24:raise ValueError('Use 1–24 meaningful source search words.')
    return terms
