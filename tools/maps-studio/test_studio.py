import importlib.util
import json
from pathlib import Path
import threading
import unittest
import urllib.request
import urllib.error
from unittest.mock import patch

BASE=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('studio',BASE/'studio.py')
studio=importlib.util.module_from_spec(spec);spec.loader.exec_module(studio)

class StudioTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server=studio.Server(('127.0.0.1',0))
        cls.thread=threading.Thread(target=cls.server.serve_forever,daemon=True);cls.thread.start()
    @classmethod
    def tearDownClass(cls):cls.server.shutdown();cls.server.server_close();cls.thread.join()
    def request(self,path,body=None,headers=None):
        h={'Host':self.server.origin.removeprefix('http://')}
        if body is not None:h.update({'Content-Type':'application/json','Origin':self.server.origin,'X-Prometheus-Token':self.server.token})
        h.update(headers or {})
        req=urllib.request.Request(self.server.origin+path,data=None if body is None else json.dumps(body).encode(),headers=h)
        try:
            with urllib.request.urlopen(req,timeout=3) as response:return response.status, response.read(), response.headers
        except urllib.error.HTTPError as error:return error.code,error.read(),error.headers
    def test_default_loopback(self):self.assertEqual(self.server.server_address[0],'127.0.0.1')
    def test_bootstrap_and_static(self):
        status,raw,headers=self.request('/api/bootstrap');self.assertEqual(status,200);self.assertEqual(json.loads(raw)['token'],self.server.token)
        status,raw,headers=self.request('/');self.assertEqual(status,200);self.assertIn(b'Maps &amp;',raw.replace(b'Maps &',b'Maps &amp;'));self.assertIn("frame-ancestors 'none'",headers['Content-Security-Policy'])
    def test_host_rebinding_blocked(self):self.assertEqual(self.request('/api/bootstrap',headers={'Host':'evil.example'})[0],403)
    def test_cross_site_bootstrap_blocked(self):self.assertEqual(self.request('/api/bootstrap',headers={'Sec-Fetch-Site':'cross-site'})[0],403)
    def test_origin_blocked(self):self.assertEqual(self.request('/api/review',{}, {'Origin':'https://evil.example'})[0],403)
    def test_token_blocked(self):self.assertEqual(self.request('/api/review',{}, {'X-Prometheus-Token':'invalid'})[0],403)
    def test_path_traversal_blocked(self):self.assertEqual(self.request('/../studio.py')[0],404)
    def test_files_are_not_exposed(self):self.assertEqual(self.request('/studio.py')[0],404)
    def test_unknown_fields_blocked(self):self.assertEqual(self.request('/api/nasa',{'query':'moon','url':'http://127.0.0.1'})[0],400)
    def test_no_general_fetch(self):
        for url in ['http://images-api.nasa.gov/search','https://localhost/','https://images-api.nasa.gov.evil.test/','https://images-api.nasa.gov:444/search','https://user@images-api.nasa.gov/']:
            with self.assertRaises(ValueError):studio.public_json(url)
    def test_review_aliases_and_redaction(self):
        text='import pickle as p\nfrom subprocess import run as r\np.loads(data)\nr(cmd, shell=True)\npassword="synthetic_dummy_credential_0000"\n'
        status,raw,_=self.request('/api/review',{'name':'example.py','source':text});self.assertEqual(status,200)
        rules={x['rule'] for x in json.loads(raw)['findings']};self.assertEqual(rules,{'unsafe-deserialization','shell-command','possible-secret'});self.assertNotIn(b'synthetic_dummy_credential_0000',raw)
    def test_publisher_redirect_keeps_head(self):
        req=urllib.request.Request('https://download.geofabrik.de/test-latest.osm.pbf',method='HEAD')
        follow=studio.PublisherRedirect().redirect_request(req,None,302,'Found',{},'https://download.geofabrik.de/test-261009.osm.pbf')
        self.assertEqual(follow.get_method(),'HEAD')
    def test_redirect_cannot_leave_origin(self):
        req=urllib.request.Request('https://download.geofabrik.de/test-latest.osm.pbf',method='HEAD')
        for target in ['http://download.geofabrik.de/a','https://localhost/a','https://cmr.earthdata.nasa.gov/a','https://user@download.geofabrik.de/a','https://download.geofabrik.de:444/a']:
            with self.assertRaises(ValueError):studio.PublisherRedirect().redirect_request(req,None,302,'Found',{},target)
    def test_redirect_hop_bound(self):
        req=urllib.request.Request('https://download.geofabrik.de/a',method='HEAD')
        handler=studio.PublisherRedirect()
        for i in range(3):req=handler.redirect_request(req,None,302,'Found',{},f'https://download.geofabrik.de/{i}')
        with self.assertRaises(ValueError):handler.redirect_request(req,None,302,'Found',{},'https://download.geofabrik.de/four')
    def test_review_no_execution(self):
        report=studio.review_source('example.py','raise RuntimeError("must not execute")');self.assertFalse(report['findings'])
    def test_bad_source_limits(self):
        for text in ['a'*200001,'a\x00b',None]:
            with self.assertRaises(ValueError):studio.review_source('example.py',text)
    def test_no_xss_in_review_response(self):
        data=studio.review_source('a.js','target.innerHTML = user;');self.assertEqual(data['findings'][0]['rule'],'html-injection')
    def test_nasa_metadata(self):
        sample={'collection':{'items':[{'data':[{'nasa_id':'unsafe/id','title':'<b>Moon</b>','date_created':'2025-01-01'}]}]}}
        with patch.object(studio,'public_json',return_value=(sample,{})):
            data=studio.nasa_media('Moon');self.assertEqual(data['results'][0]['url'],'https://images.nasa.gov/details/unsafe%2Fid');self.assertIn('retrieved_utc',data)
    def test_cached_requests_do_not_refetch(self):
        services=studio.Services()
        with patch.object(studio,'nasa_media',return_value={'retrieved_utc':'2026-10-09','results':[]}) as call:
            one=services.request('nasa',{'query':'moon'});two=services.request('nasa',{'query':'moon'});self.assertFalse(one['cached']);self.assertTrue(two['cached']);self.assertEqual(call.call_count,1)
    def test_invalid_region_not_fetched(self):
        with self.assertRaises(ValueError):studio.Services().request('map_freshness',{'region':'http://localhost'})
    def test_catalog_is_https(self):
        data=json.loads((BASE/'catalog.json').read_text(encoding='utf-8'))
        for group in data.values():
            if isinstance(group,list):
                for item in group:self.assertTrue(item['url'].startswith('https://'),item['url'])

if __name__=='__main__':unittest.main(verbosity=2)
