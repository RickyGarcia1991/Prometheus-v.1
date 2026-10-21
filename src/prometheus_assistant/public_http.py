"""Bounded HTTPS requests to explicit public publishers, with DNS pinning."""
import http.client
import ipaddress
import json
import math
import queue
import socket
import ssl
import threading
import time
from urllib.parse import urlsplit

MAX_PUBLIC_BYTES=2_000_000
_DNS_SLOT=threading.BoundedSemaphore(1)
_POLL_SECONDS=.025


class _RequestLimit:
    def __init__(self,overall_timeout,cancel_event):
        if (isinstance(overall_timeout,bool) or not isinstance(overall_timeout,(int,float))
            or not math.isfinite(overall_timeout) or not 0<overall_timeout<=45):
            raise ValueError('Invalid public request overall timeout (must be >0 and <=45 seconds).')
        self.deadline=time.monotonic()+overall_timeout
        self.cancel_event=cancel_event

    def check(self):
        if self.cancel_event is not None and self.cancel_event.is_set():
            raise RuntimeError('Public-source request cancelled.')
        if time.monotonic()>=self.deadline:
            raise RuntimeError('Public-source request exceeded its time limit.')

    def remaining(self):
        self.check()
        return max(.001,self.deadline-time.monotonic())


def _resolve(host,limit):
    # A timed-out OS resolver cannot be killed safely. Keep at most one such
    # daemon outstanding; later callers wait cancellably instead of spawning
    # unbounded workers when DNS is stuck. The slot recovers when DNS returns.
    while True:
        limit.check()
        if _DNS_SLOT.acquire(timeout=min(_POLL_SECONDS,limit.remaining())):break
    result=queue.Queue(maxsize=1)

    def resolve():
        try:
            try:result.put((True,socket.getaddrinfo(host,443,type=socket.SOCK_STREAM)))
            except Exception as error:result.put((False,error))
        finally:_DNS_SLOT.release()

    try:
        limit.check()
        threading.Thread(target=resolve,name='Prometheus-public-DNS',daemon=True).start()
    except BaseException:
        _DNS_SLOT.release()
        raise
    while True:
        limit.check()
        try:ok,value=result.get(timeout=min(_POLL_SECONDS,limit.remaining()))
        except queue.Empty:continue
        limit.check()
        if not ok:raise value
        return value

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
        self._transport=None
        self._transport_lock=threading.Lock()
        self._aborted=False
        self._request_limit=None

    def _track_transport(self,transport):
        with self._transport_lock:
            if self._aborted:
                transport.close()
                raise OSError('Public-source connection was interrupted.')
            self._transport=transport

    def _socket_timeout(self):
        if self._request_limit is None:return self.timeout
        remaining=self._request_limit.remaining()
        # Socket close interrupts active I/O; this short fallback also bounds
        # platforms that take longer to interrupt a connect or TLS handshake.
        if self._request_limit.cancel_event is not None:remaining=min(remaining,2)
        return min(self.timeout,remaining)

    def connect(self):
        family=socket.AF_INET6 if ipaddress.ip_address(self.address).version==6 else socket.AF_INET
        raw=socket.socket(family,socket.SOCK_STREAM)
        self._track_transport(raw)
        try:
            raw.settimeout(self._socket_timeout())
            # Direct numeric connect avoids even a second getaddrinfo call.
            raw.connect((self.address,443))
            wrapped=self._context.wrap_socket(raw,server_hostname=self.host,do_handshake_on_connect=False)
            self._track_transport(wrapped)
            wrapped.settimeout(self._socket_timeout())
            wrapped.do_handshake()
            # Connect/TLS uses a short fallback for cancellation on platforms
            # that do not interrupt those stages promptly. After the handshake,
            # allow the registered service its response time; the watchdog still
            # shuts down the socket on cancellation or the overall deadline.
            wrapped.settimeout(min(self.timeout, self._request_limit.remaining())
                               if self._request_limit is not None else self.timeout)
            self.sock=wrapped
        except BaseException:
            self.abort()
            raise

    def abort(self):
        with self._transport_lock:
            self._aborted=True
            transport=self._transport
        if transport is not None:
            # close() alone may leave an HTTPResponse.makefile read alive.
            try:transport.shutdown(socket.SHUT_RDWR)
            except OSError:pass
            transport.close()

    def close(self):
        super().close()
        with self._transport_lock:
            transport=self._transport
            # HTTPConnection closes itself as soon as Connection: close
            # headers arrive, while its response file still owns the socket.
            # Retain that socket so the watchdog can interrupt the body read.
        if transport is not None:transport.close()


def _watch_request(connection,limit,finished):
    while not finished.wait(_POLL_SECONDS):
        try:limit.check()
        except RuntimeError:
            abort=getattr(connection,'abort',connection.close)
            abort()
            return

class PublicHTTPError(RuntimeError):
    """HTTP status only: provider bodies and credentials never enter errors."""
    def __init__(self, status):
        self.status = status
        super().__init__(f'Publisher returned HTTP {status}; no alternate endpoint was contacted.')


def fetch_public_bytes(url,allowed_hosts,*,timeout=20,max_bytes=MAX_PUBLIC_BYTES,accept='application/json',
                       cancel_event=None,overall_timeout=45):
    return _request_public_bytes(url, allowed_hosts, timeout=timeout, max_bytes=max_bytes,
                                 accept=accept, cancel_event=cancel_event, overall_timeout=overall_timeout)


def post_public_json(url, allowed_hosts, payload, *, bearer_token=None, timeout=20,
                     max_bytes=MAX_PUBLIC_BYTES, cancel_event=None, overall_timeout=45):
    from .application_policy import require_dispatch
    require_dispatch('public.fetch')
    if not isinstance(payload, dict):
        raise ValueError('A JSON object is required for public search.')
    body = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode('utf-8')
    if len(body) > 20_000:
        raise ValueError('Public search request exceeded the size limit.')
    if bearer_token is not None and (not isinstance(bearer_token, str) or not 1 <= len(bearer_token) <= 512
                                    or any(ord(c) < 33 or ord(c) > 126 for c in bearer_token)):
        raise ValueError('Invalid public search API key format.')
    headers = {'Content-Type': 'application/json'}
    if bearer_token:
        headers['Authorization'] = 'Bearer ' + bearer_token
    return _request_public_bytes(url, allowed_hosts, method='POST', body=body, extra_headers=headers,
                                 timeout=timeout, max_bytes=max_bytes, cancel_event=cancel_event,
                                 overall_timeout=overall_timeout)


def _request_public_bytes(url,allowed_hosts,*,method='GET',body=None,extra_headers=None,
                          timeout=20,max_bytes=MAX_PUBLIC_BYTES,accept='application/json',
                          cancel_event=None,overall_timeout=45):
    from .application_policy import require_dispatch
    require_dispatch('public.fetch')
    if type(max_bytes) is not int or not 1<=max_bytes<=20_000_000:
        raise ValueError('Invalid public download limit.')
    if timeout is not None and (isinstance(timeout,bool) or not isinstance(timeout,(int,float))
        or not math.isfinite(timeout) or timeout<=0):
        raise ValueError('Invalid public request socket timeout.')
    limit=_RequestLimit(overall_timeout,cancel_event)
    parsed=validate_public_url(url,allowed_hosts)
    records=_resolve(parsed.hostname,limit)
    addresses=public_addresses([row[4][0] for row in records])
    # Connecting to this already-validated address prevents a second DNS lookup
    # from resolving the approved hostname to a private/local service.
    connection=PinnedHTTPSConnection(parsed.hostname,addresses[0],
                                     min(timeout if timeout is not None else 45,limit.remaining()))
    connection._request_limit=limit
    finished=threading.Event()
    watcher=threading.Thread(target=_watch_request,args=(connection,limit,finished),
                             name='Prometheus-public-deadline',daemon=True)
    response=None
    watcher.start()
    try:
        limit.check()
        path=parsed.path or '/'
        if parsed.query:path+='?'+parsed.query
        headers = {'Accept':accept,'Accept-Encoding':'identity',
            'User-Agent':'Prometheus-PublicReference/1.0 (bounded user-initiated research)'}
        headers.update(extra_headers or {})
        if method == 'GET':
            connection.request('GET',path,headers=headers)
        else:
            connection.request(method,path,body=body,headers=headers)
        response=connection.getresponse()
        limit.check()
        if 300<=response.status<400:raise RuntimeError('Publisher redirects are refused; update the registered endpoint.')
        if response.status!=200:raise PublicHTTPError(response.status)
        if response.getheader('Content-Encoding','identity').lower() not in ('identity',''):
            raise RuntimeError('Unexpected compressed response.')
        length=response.getheader('Content-Length')
        if length is not None and (not length.isdigit() or int(length)>max_bytes):
            raise RuntimeError('Public-source response exceeded the size limit.')
        expected=int(length) if length is not None else None
        parts=[];size=0
        while True:
            limit.check()
            chunk=response.read1(min(65536,max_bytes-size+1))
            limit.check()
            if not chunk:break
            size+=len(chunk)
            if size>max_bytes:raise RuntimeError('Public-source response exceeded the size limit.')
            parts.append(chunk)
        if expected is not None and size!=expected:
            raise RuntimeError('Public-source response was incomplete (truncated Content-Length payload).')
        return b''.join(parts)
    except http.client.IncompleteRead as error:
        limit.check()
        raise RuntimeError('Public-source response was incomplete (truncated payload).') from error
    except (OSError,http.client.HTTPException):
        limit.check()
        raise
    finally:
        finished.set()
        if response is not None:
            close=getattr(response,'close',None)
            if close is not None:close()
        connection.close()
        watcher.join(.2)

def fetch_public_json(url,allowed_hosts,*,timeout=20,cancel_event=None,overall_timeout=45):
    return fetch_public_bytes(url,allowed_hosts,timeout=timeout,cancel_event=cancel_event,
                              overall_timeout=overall_timeout)
