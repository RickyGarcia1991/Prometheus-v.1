import pytest
from prometheus_assistant import public_http as http
from prometheus_assistant.public_resources import safe_reference_url

class Response:
    status=200
    def __init__(self,headers=None,body=b'{}',status=200):self.headers=headers or {};self.body=body;self.status=status
    def getheader(self,key,default=None):return self.headers.get(key,default)
    def read1(self,size):out,self.body=self.body[:size],self.body[size:];return out

def setup(monkeypatch,response,address='93.184.216.34'):
    calls=[]
    monkeypatch.setattr(http.socket,'getaddrinfo',lambda *a,**k:[(None,None,None,None,(address,443))])
    class Connection:
        def __init__(self,host,ip,timeout):calls.append((host,ip))
        def request(self,*a,**k):calls.append(('GET',a,k))
        def getresponse(self):return response
        def close(self):calls.append('closed')
    monkeypatch.setattr(http,'PinnedHTTPSConnection',Connection)
    return calls

@pytest.mark.parametrize('response',[Response(status=302),Response(status=403),Response({'Content-Length':'2000001'}),Response({'Content-Encoding':'gzip'}),Response(body=b'x'*2000001)])
def test_refuses_redirect_errors_compression_oversize(monkeypatch,response):
    calls=setup(monkeypatch,response)
    with pytest.raises(RuntimeError):http.fetch_public_json('https://example.org/data',{'example.org'})
    assert calls[-1]=='closed'

def test_connects_only_to_validated_numeric_address(monkeypatch):
    calls=setup(monkeypatch,Response())
    assert http.fetch_public_json('https://example.org/data',{'example.org'})==b'{}'
    assert calls[0]==('example.org','93.184.216.34')

def test_private_dns_refused_before_connect(monkeypatch):
    calls=setup(monkeypatch,Response(),address='127.0.0.1')
    with pytest.raises(ValueError):http.fetch_public_json('https://example.org/data',{'example.org'})
    assert calls==[]

@pytest.mark.parametrize('link',['https://localhost/x','https://127.0.0.1/x','https://192.168.1.4/x','https://[::1]/x','https://x.local/x','file:///x','https://example.org:9443/x','https://example.org/\r\nx','https://u:p@example.org/'])
def test_private_or_unsafe_result_links_not_presented(link):assert not safe_reference_url(link)
