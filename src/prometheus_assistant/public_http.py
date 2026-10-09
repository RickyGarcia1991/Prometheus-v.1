"""Bounded HTTPS GETs to explicit public publishers, with DNS pinning."""
import http.client
import ipaddress
import socket
import ssl
import time
from urllib.parse import urlsplit

MAX_PUBLIC_BYTES=2_000_000

def validate_public_url(url,allowed_hosts):
    if not isinstance(url,str) or len(url)>6000 or any(ord(c)<33 for c in url) or '\\' in url:
        raise ValueError('Invalid public-source URL.')
    try:
        parsed=urlsplit(url);port=parsed.port
    except ValueError as error:raise ValueError('Invalid public-source URL.') from error
    if (parsed.scheme!='https' or parsed.hostname not in allowed_hosts or port not in (None,443)
        or parsed.username or parsed.password or parsed.fragment):
        raise ValueError('Only the registered HTTPS publisher endpoint is allowed.')
    return parsed

def public_addresses(addresses):
    result=[]
    for address in addresses:
        try:parsed=ipaddress.ip_address(address)
        except ValueError as error:raise ValueError('Publisher DNS returned an invalid address.') from error
        if not parsed.is_global or getattr(parsed,'ipv4_mapped',None) is not None and not parsed.ipv4_mapped.is_global:
            raise ValueError('Publisher DNS returned a nonpublic address; request refused.')
        if str(parsed) not in result:result.append(str(parsed))
    if not result:raise ValueError('Publisher DNS returned no usable addresses.')
    return result

class PinnedHTTPSConnection(http.client.HTTPSConnection):
    def __init__(self,host,address,timeout):
        super().__init__(host,port=443,timeout=timeout,context=ssl.create_default_context())
        self.address=address
    def connect(self):
        raw=socket.create_connection((self.address,443),self.timeout)
        try:self.sock=self._context.wrap_socket(raw,server_hostname=self.host)
        except Exception:raw.close();raise

def fetch_public_bytes(url,allowed_hosts,*,timeout=20,max_bytes=MAX_PUBLIC_BYTES,accept='application/json'):
    if type(max_bytes) is not int or not 1<=max_bytes<=20_000_000:
        raise ValueError('Invalid public download limit.')
    parsed=validate_public_url(url,allowed_hosts)
    records=socket.getaddrinfo(parsed.hostname,443,type=socket.SOCK_STREAM)
    addresses=public_addresses([row[4][0] for row in records])
    # Connecting to this already-validated address prevents a second DNS lookup
    # from resolving the approved hostname to a private/local service.
    connection=PinnedHTTPSConnection(parsed.hostname,addresses[0],timeout)
    try:
        path=parsed.path or '/'
        if parsed.query:path+='?'+parsed.query
        connection.request('GET',path,headers={'Accept':accept,'Accept-Encoding':'identity',
            'User-Agent':'Prometheus-PublicReference/1.0 (bounded user-initiated research)'})
        response=connection.getresponse()
        if 300<=response.status<400:raise RuntimeError('Publisher redirects are refused; update the registered endpoint.')
        if response.status!=200:raise RuntimeError(f'Publisher returned HTTP {response.status}; no alternate endpoint was contacted.')
        if response.getheader('Content-Encoding','identity').lower() not in ('identity',''):
            raise RuntimeError('Unexpected compressed response.')
        length=response.getheader('Content-Length')
        if length is not None and (not length.isdigit() or int(length)>max_bytes):
            raise RuntimeError('Public-source response exceeded the size limit.')
        parts=[];size=0;deadline=time.monotonic()+45
        while True:
            if time.monotonic()>deadline:raise RuntimeError('Public-source response exceeded its time limit.')
            chunk=response.read1(min(65536,max_bytes-size+1))
            if not chunk:break
            size+=len(chunk)
            if size>max_bytes:raise RuntimeError('Public-source response exceeded the size limit.')
            parts.append(chunk)
        return b''.join(parts)
    finally:connection.close()

def fetch_public_json(url,allowed_hosts,*,timeout=20):
    return fetch_public_bytes(url,allowed_hosts,timeout=timeout)
