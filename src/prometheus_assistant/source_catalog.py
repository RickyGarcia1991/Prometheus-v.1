from __future__ import annotations
from dataclasses import dataclass
from urllib.parse import urlencode

@dataclass(frozen=True)
class RemoteSource:
    id: str
    title: str
    authority: int
    subjects: tuple[str,...]
    base_url: str
    access: str
    notes: str

SOURCES=(
 RemoteSource("wiktionary","Wiktionary",80,("dictionary","language","etymology","translations"),"https://en.wiktionary.org","public-api","Broad multilingual community dictionary; verify specialist claims."),
 RemoteSource("wikipedia","Wikipedia",70,("general","history","science","math","geography"),"https://en.wikipedia.org","public-api","Broad tertiary reference; follow citations for high-stakes claims."),
 RemoteSource("loc","Library of Congress",95,("books","history","maps","manuscripts","newspapers","media"),"https://www.loc.gov","public-api","Primary library/digital-collection source."),
 RemoteSource("crossref","Crossref",90,("research","journals","books","doi","scholarship"),"https://api.crossref.org","public-api","Scholarly metadata; content access varies by publisher."),
 RemoteSource("datagov","Data.gov",95,("government","datasets","federal","state","local"),"https://catalog.data.gov","public-api","Government dataset catalog; links to authoritative publishers."),
 RemoteSource("nasa","NASA",100,("space","astronomy","earth","science"),"https://api.nasa.gov","public-api","Primary U.S. space/science source; some high-volume endpoints use a key."),
 RemoteSource("reddit","Reddit",35,("community","experiences","reviews","troubleshooting"),"https://www.reddit.com","api-policy","Community evidence only; never authoritative by itself."),
 RemoteSource("openstax","OpenStax",90,("math","science","history","humanities","textbooks"),"https://openstax.org","web","Openly licensed educational textbooks."),
 RemoteSource("gutenberg","Project Gutenberg",85,("books","literature","history","philosophy"),"https://www.gutenberg.org","web","Public-domain/free ebooks; rights vary by jurisdiction."),
)

def source_catalog():
 return [s.__dict__ for s in sorted(SOURCES,key=lambda x:(-x.authority,x.id))]

def wikimedia_search_url(project:str, query:str)->str:
 if project not in {"wikipedia","wiktionary"}: raise ValueError("unsupported Wikimedia project")
 return f"https://en.{project}.org/w/api.php?"+urlencode({"action":"query","list":"search","srsearch":query,"format":"json","utf8":1})

def loc_search_url(query:str)->str:
 return "https://www.loc.gov/search/?"+urlencode({"q":query,"fo":"json","c":10})

def crossref_search_url(query:str)->str:
 return "https://api.crossref.org/works?"+urlencode({"query":query,"rows":10})
