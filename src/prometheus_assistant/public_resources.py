"""Explicit public research connectors. Results are evidence, never commands."""
from datetime import datetime,timezone
from html.parser import HTMLParser
import hashlib,json,re,ipaddress
from urllib.parse import urlencode,urlsplit,quote
from .public_http import fetch_public_json,MAX_PUBLIC_BYTES

PROVIDERS={
 'federal-register':('Federal Register','government,law,regulations','U.S.','www.federalregister.gov','https://www.federalregister.gov/developers/documentation/api/v1'),
 'ecfr':('Electronic Code of Federal Regulations','law,regulations','U.S.','www.ecfr.gov','https://www.ecfr.gov/developers/documentation/api/v1'),
 'europe-pmc':('Europe PMC','medicine,biology,research','International','www.ebi.ac.uk','https://europepmc.org/RestfulWebService'),
 'clinicaltrials':('ClinicalTrials.gov','medicine,clinical-research','International','clinicaltrials.gov','https://clinicaltrials.gov/data-api/api'),
 'crossref':('Crossref','science,research,books,journals','International','api.crossref.org','https://www.crossref.org/documentation/retrieve-metadata/rest-api/'),
 'nasa':('NASA Image and Video Library','space,earth,science','International','images-api.nasa.gov','https://images.nasa.gov/docs/images.nasa.gov_api_docs.pdf'),
 'loc':('Library of Congress','history,government,books,maps','International','www.loc.gov','https://www.loc.gov/apis/json-and-yaml/'),
 'canada-open-data':('Government of Canada Open Data','government,science,economics','Canada','open.canada.ca','https://open.canada.ca/en/access-our-application-programming-interface-api'),
}

class TextOnly(HTMLParser):
    def __init__(self):super().__init__(convert_charrefs=True);self.parts=[];self.hidden=0
    def handle_starttag(self,tag,attrs):
        if tag in ('script','style','iframe','object'):self.hidden+=1
        elif not self.hidden:self.parts.append(' ')
    def handle_endtag(self,tag):
        if tag in ('script','style','iframe','object') and self.hidden:self.hidden-=1
        elif not self.hidden:self.parts.append(' ')
    def handle_data(self,text):
        if not self.hidden:self.parts.append(text)

def text_only(value,limit=1800):
    if isinstance(value,list):value=' '.join(v for v in value if isinstance(v,str))
    if not isinstance(value,str):return ''
    parser=TextOnly();parser.feed(value[:100_000])
    return re.sub(r'\s+',' ',''.join(parser.parts)).strip()[:limit]

def provider_catalog():
    return [dict(id=key,title=row[0],subjects=row[1].split(','),jurisdiction=row[2],documentation=row[4],
                 connector_implemented=True,network_requires_explicit_approval=True,
                 last_live_check='See deployment acceptance report; availability can change.') for key,row in PROVIDERS.items()]

def safe_reference_url(link):
    if not isinstance(link,str) or len(link)>2000 or '\\' in link or any(ord(c)<33 for c in link):return False
    try:
        parsed=urlsplit(link)
        if parsed.scheme!='https' or not parsed.hostname or parsed.username or parsed.password or parsed.port not in (None,443):return False
        host=parsed.hostname.lower().rstrip('.')
        if host in ('localhost','localhost.localdomain') or host.endswith(('.localhost','.local','.internal')):return False
        try:return ipaddress.ip_address(host).is_global
        except ValueError:return '.' in host
    except ValueError:return False

class PublicResearchProvider:
    def __init__(self,provider='crossref'):self.provider=provider
    def search(self,query):
        from .research import ResearchResult,ResearchSource
        result=public_search(query,provider=self.provider,online=True)
        return ResearchResult(query,tuple(ResearchSource.from_content(url=r['url'],title=r['title'],
            content=json.dumps(r,ensure_ascii=False),excerpt=json.dumps(r,ensure_ascii=False)[:1800]) for r in result['results']))

def endpoint(provider,query,limit):
    if provider=='federal-register':return 'https://www.federalregister.gov/api/v1/documents.json?'+urlencode({'conditions[term]':query,'per_page':limit,'order':'relevance'})
    if provider=='ecfr':return 'https://www.ecfr.gov/api/search/v1/results?'+urlencode({'query':query,'per_page':limit})
    if provider=='europe-pmc':return 'https://www.ebi.ac.uk/europepmc/webservices/rest/search?'+urlencode({'query':query,'format':'json','resultType':'core','pageSize':limit})
    if provider=='clinicaltrials':return 'https://clinicaltrials.gov/api/v2/studies?'+urlencode({'query.term':query,'pageSize':limit,'format':'json'})
    if provider=='crossref':return 'https://api.crossref.org/works?'+urlencode({'query':query,'rows':limit})
    if provider=='nasa':return 'https://images-api.nasa.gov/search?'+urlencode({'q':query,'page_size':limit,'media_type':'image'})
    if provider=='loc':return 'https://www.loc.gov/search/?'+urlencode({'q':query,'fo':'json','c':limit})
    if provider=='canada-open-data':return 'https://open.canada.ca/data/en/api/3/action/package_search?'+urlencode({'q':query,'rows':limit})
    raise ValueError('Unknown public research provider.')

def _items(payload,provider):
    if not isinstance(payload,dict):raise RuntimeError('Publisher returned an invalid response object.')
    if provider in ('federal-register','ecfr','loc'):items=payload.get('results')
    elif provider=='europe-pmc':items=payload.get('resultList',{}).get('result')
    elif provider=='clinicaltrials':items=payload.get('studies')
    elif provider=='crossref':items=payload.get('message',{}).get('items')
    elif provider=='nasa':items=payload.get('collection',{}).get('items')
    else:items=payload.get('result',{}).get('results')
    if not isinstance(items,list):raise RuntimeError('Publisher response did not contain the expected result list.')
    return items

def _record(item,provider):
    if provider=='federal-register':return item.get('title'),item.get('html_url'),item.get('abstract'),item.get('publication_date'),item.get('document_number'),item.get('type')
    if provider=='ecfr':
        hierarchy=item.get('hierarchy',{});title=hierarchy.get('title');section=hierarchy.get('section')
        url='https://www.ecfr.gov/current/title-'+quote(str(title),safe='')
        if section:url+='/section-'+quote(str(section),safe='')
        return item.get('headings',{}).get('section') or item.get('full_text_excerpt') or 'eCFR result',url,item.get('full_text_excerpt'),item.get('starts_on'),section,'regulation'
    if provider=='europe-pmc':
        source=item.get('source','MED');identity=str(item.get('id',''))
        return item.get('title'),'https://europepmc.org/article/'+quote(str(source),safe='')+'/'+quote(identity,safe=''),item.get('abstractText'),item.get('firstPublicationDate'),identity,('preprint' if source=='PPR' else 'research article')
    if provider=='clinicaltrials':
        protocol=item.get('protocolSection',{});identity=protocol.get('identificationModule',{});status=protocol.get('statusModule',{})
        return identity.get('briefTitle'),'https://clinicaltrials.gov/study/'+quote(str(identity.get('nctId','')),safe=''),protocol.get('descriptionModule',{}).get('briefSummary'),status.get('lastUpdateSubmitDate'),identity.get('nctId'),'study registration; not proof of treatment effectiveness'
    if provider=='crossref':
        date_parts=item.get('published',{}).get('date-parts',[[]]);published='-'.join(map(str,date_parts[0])) if date_parts else ''
        return item.get('title'),'https://doi.org/'+quote(str(item.get('DOI','')),safe='/'),item.get('abstract') or 'Bibliographic metadata; full-text access depends on the publisher.',published,item.get('DOI'),item.get('type')
    if provider=='nasa':
        data=item.get('data',[{}])[0];identity=data.get('nasa_id','')
        return data.get('title'),'https://images.nasa.gov/details/'+quote(str(identity),safe=''),data.get('description'),data.get('date_created'),identity,'NASA media metadata'
    if provider=='loc':return item.get('title'),item.get('url') or item.get('id'),item.get('description'),item.get('date'),item.get('id'),'catalog record; item rights vary'
    title=item.get('title');title=item.get('title_translated',{}).get('en',title)
    notes=item.get('notes_translated',{}).get('en',item.get('notes'))
    return title,'https://open.canada.ca/data/en/dataset/'+quote(str(item.get('id','')),safe=''),notes,item.get('metadata_modified'),item.get('id'),'government dataset metadata'

def public_search(query,*,provider,online=False,limit=5,fetch=None):
    if not online:raise ValueError('Public research requires explicit online permission.')
    if provider not in PROVIDERS:raise ValueError('Unknown public research provider.')
    if not isinstance(query,str) or not query.strip() or len(query)>400 or any(ord(c)<32 for c in query):raise ValueError('Use a public research query of 1–400 characters.')
    if type(limit) is not int or not 1<=limit<=10:raise ValueError('Public result limit must be 1–10.')
    url=endpoint(provider,query.strip(),limit)
    body=(fetch or (lambda u:fetch_public_json(u,{PROVIDERS[provider][3]})))(url)
    if not isinstance(body,bytes) or len(body)>MAX_PUBLIC_BYTES:raise RuntimeError('Public response exceeded its size limit.')
    try:payload=json.loads(body)
    except (ValueError,UnicodeError) as error:raise RuntimeError('Publisher returned invalid JSON.') from error
    try:items=_items(payload,provider)
    except (AttributeError,TypeError) as error:raise RuntimeError('Publisher returned an invalid result schema.') from error
    rows=[]
    for item in items[:limit]:
        if not isinstance(item,dict):continue
        try:
            title,link,excerpt,published,identity,kind=_record(item,provider)
            if not safe_reference_url(link):continue
            title=text_only(title,400)
            if not title:continue
            record=dict(title=title,url=link,excerpt=text_only(excerpt),published=text_only(published,80),
                source_id=text_only(str(identity or ''),250),document_type=text_only(kind,160))
            if provider=='europe-pmc':record.update(retraction_flag=item.get('isRetracted','unknown'),open_access_flag=item.get('isOpenAccess','unknown'))
            if provider=='clinicaltrials':record['study_status']=text_only(item.get('protocolSection',{}).get('statusModule',{}).get('overallStatus'),100)
            rows.append(record)
        except (AttributeError,TypeError,IndexError,ValueError):continue
    return dict(query=query,provider=provider,publisher=PROVIDERS[provider][0],jurisdiction=PROVIDERS[provider][2],
                retrieved_utc=datetime.now(timezone.utc).isoformat(),response_sha256=hashlib.sha256(body).hexdigest(),
                offline=False,evidence_is_untrusted_data=True,code_executed=False,generated_answer=False,
                full_text_guaranteed=False,results=rows)
