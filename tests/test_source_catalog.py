from prometheus_assistant.source_catalog import source_catalog,wikimedia_search_url,loc_search_url,crossref_search_url
def test_sources_rank_authority():
 rows=source_catalog(); scores=[r["authority"] for r in rows]
 assert scores==sorted(scores,reverse=True)
 assert next(r for r in rows if r["id"]=="reddit")["authority"] < next(r for r in rows if r["id"]=="loc")["authority"]
def test_query_urls_encode():
 assert "srsearch=black+holes" in wikimedia_search_url("wiktionary","black holes")
 assert "fo=json" in loc_search_url("civil war")
 assert "rows=10" in crossref_search_url("quantum mechanics")
