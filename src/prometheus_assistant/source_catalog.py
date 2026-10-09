from __future__ import annotations
from dataclasses import dataclass
from urllib.parse import urlencode
from pathlib import Path
import json

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
 RemoteSource("raspberry-pi","Raspberry Pi official documentation",95,("hardware","raspberry-pi","pico","gpio","camera","sensors"),"https://www.raspberrypi.com/documentation/","open-source","Official documentation and Pico SDK/examples are saved offline at pinned revisions. Match instructions to the exact board, voltage and peripheral."),
 RemoteSource("arduino","Arduino official documentation",95,("hardware","arduino","microcontrollers","sensors","coding"),"https://docs.arduino.cc","open-source","Offline examples and AVR core sources accompany references; downloading references does not flash or validate attached hardware."),
 RemoteSource("espressif","Espressif documentation",95,("hardware","esp32","wifi","microcontrollers"),"https://docs.espressif.com","open-source","ESP32 Arduino core reference saved offline. Choose the correct SoC and board; toolchains are separate."),
 RemoteSource("kicad","KiCad documentation and libraries",95,("hardware","pcb","electronics","schematics"),"https://docs.kicad.org","open-source","Official documentation, symbols, footprints and templates retained offline with licenses. Library metadata search does not replace design-rule checks."),
 RemoteSource("circuitpython","CircuitPython documentation",95,("hardware","sensors","microcontrollers","drivers"),"https://docs.circuitpython.org","open-source","Source driver bundle is retained with per-library notices. Device and firmware compatibility still require checking."),
 RemoteSource("openroad","OpenROAD documentation",95,("hardware","chips","asic","physical-design"),"https://openroad.readthedocs.io/en/latest/","open-source","Pinned source/reference material only. Execution requires a compatible toolchain and process design kit."),
 RemoteSource("yosys","Yosys synthesis documentation",95,("hardware","chips","fpga","verilog","synthesis"),"https://yosyshq.readthedocs.io/projects/yosys/en/latest/","open-source","Pinned source and examples retained offline; synthesis runtime and target FPGA tools are separate."),
 RemoteSource("stacks-project","Stacks Project",95,("math","algebra","algebraic-geometry","topology","categories"),"https://stacks.math.columbia.edu","open-source","Graduate and research mathematics; pinned original TeX source is indexed offline with commit and line citations. Preserve GNU FDL notices."),
 RemoteSource("mathlib4","Lean mathematical library",95,("math","proofs","algebra","analysis","geometry","number-theory"),"https://github.com/leanprover-community/mathlib4","open-source","Apache-2.0 formal mathematical sources indexed offline. Retrieval is not a local proof-verification run; the Lean compiler is separate."),
 RemoteSource("nist-dlmf","NIST Digital Library of Mathematical Functions",100,("math","special-functions","analysis","numerical-methods"),"https://dlmf.nist.gov","web","Online reference only: DLMF permits limited research/teaching copying and explicitly prohibits bulk copying. Respect its notices and cite the version."),
 RemoteSource("open-library","Open Library",85,("books","authors","editions","bibliography","history"),"https://openlibrary.org","public-api","Book discovery and metadata; not a guarantee of full-text availability. Use monthly data dumps for bulk metadata and respect API limits."),
 RemoteSource("nist-constants","NIST CODATA physical constants",100,("science","physics","units","constants","measurement"),"https://physics.nist.gov/cuu/Constants/","web","Primary reference with uncertainties and prior editions. Record the data edition; historical values remain useful for comparison."),
 RemoteSource("medlineplus","MedlinePlus — National Library of Medicine",100,("medicine","health","medical-tests","medical-terms"),"https://medlineplus.gov","public-data","Public-domain health-topic summaries have an offline index; licensed encyclopedia and drug articles remain external links. Check dates before clinical use."),
 RemoteSource("pmc","PubMed Central Open Access Subset",95,("medicine","biology","research","papers"),"https://pmc.ncbi.nlm.nih.gov/tools/openftlist/","public-api","Use authorized bulk/API services and per-article licenses. Distinguish preprints, peer-reviewed studies, corrections, and retractions."),
 RemoteSource("ncbi-bookshelf","NCBI Bookshelf",95,("medicine","biology","books","guidelines"),"https://www.ncbi.nlm.nih.gov/books/","web","Medical and scientific books; automated full-text reuse is limited to eligible material and its terms."),
 RemoteSource("mit-ocw","MIT OpenCourseWare",95,("math","algebra","calculus","science","engineering","computing"),"https://ocw.mit.edu","web","Course notes, assignments and teaching material. Preserve course edition and item-specific licenses."),
 RemoteSource("sympy","SymPy documentation",95,("math","algebra","calculus","symbolic-computation"),"https://docs.sympy.org","web","Official symbolic calculation documentation. Catalog entry does not mean the solver is installed or arbitrary formulas are safe to execute."),
 RemoteSource("gsa-forms","GSA Forms Library",100,("government","forms","applications"),"https://www.gsa.gov/forms-library","web","Official U.S. federal forms; check agency, jurisdiction, revision and obsolete status before use."),
 RemoteSource("digital-bodleian","Digital Bodleian — University of Oxford",95,("history","manuscripts","rare-books","maps","languages"),"https://digital.bodleian.ox.ac.uk","web","Digitized historical collections. Check item rights; distinguish scans, transcriptions and modern interpretation."),
 RemoteSource("digivatlib","Digital Vatican Library",95,("history","manuscripts","rare-books","classics","religion"),"https://digi.vatlib.it","web","Publicly digitized manuscripts and historical material; not the entire physical collection. Check reproduction terms before copying."),
 RemoteSource("gallica","Gallica — Bibliothèque nationale de France",95,("history","books","manuscripts","newspapers","maps","french"),"https://gallica.bnf.fr","web","Digitized national-library collections. Online access, automated retrieval and reuse rights vary by item."),
 RemoteSource("openai-developers","OpenAI developer documentation",100,("ai","coding","api","models","voice"),"https://developers.openai.com","web","Official OpenAI documentation; distinguish downloadable models from hosted services."),
 RemoteSource("hermes-docs","Hermes Agent documentation",95,("coding","ai","agents"),"https://hermes-agent.nousresearch.com/docs","web","Official Nous Research Hermes Agent documentation and compatibility requirements."),
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
 from .public_resources import PROVIDERS
 rows={s.id:{**s.__dict__, "authority_is_accuracy_percentage": False,
          "catalog_entry_is_active_connector": s.id in PROVIDERS} for s in SOURCES}
 for record in json.loads(Path(__file__).with_name('public_directory.json').read_text(encoding='utf-8')):
  record['catalog_entry_is_active_connector']=record['id'] in PROVIDERS
  rows[record['id']]=record
 return sorted(rows.values(),key=lambda x:(-x['authority'],x['id']))

def wikimedia_search_url(project:str, query:str)->str:
 if project not in {"wikipedia","wiktionary"}: raise ValueError("unsupported Wikimedia project")
 return f"https://en.{project}.org/w/api.php?"+urlencode({"action":"query","list":"search","srsearch":query,"format":"json","utf8":1})

def loc_search_url(query:str)->str:
 return "https://www.loc.gov/search/?"+urlencode({"q":query,"fo":"json","c":10})

def crossref_search_url(query:str)->str:
 return "https://api.crossref.org/works?"+urlencode({"query":query,"rows":10})
