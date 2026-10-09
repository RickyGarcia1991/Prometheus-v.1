import sys
from pathlib import Path
import threading
import time
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_studio
studio = test_studio.studio


class PublisherRegressionTests(test_studio.StudioTests):
    def test_malformed_publisher_shapes_are_controlled(self):
        for method, payload in [(studio.nasa_media, {'collection': None}),
                (studio.nasa_media, {'collection': {'items': [{'data': [None]}]}}),
                (studio.earthdata, {'feed': None}),
                (lambda _: studio.map_regions(), {'features': [{'properties': None}]})]:
            with self.subTest(payload=payload), patch.object(studio, 'public_json', return_value=(payload, {})):
                with self.assertRaises(studio.PublisherResponseError): method('moon')

    def test_bad_schema_returns_json_502(self):
        with patch.object(self.server.services, 'request', side_effect=studio.PublisherResponseError()):
            code, raw, _ = self.request('/api/nasa', {'query': 'moon'})
            self.assertEqual(code, 502)
            self.assertIn(b'unsupported response', raw)

    def test_timeout_has_one_worker_and_does_not_queue(self):
        service = studio.Services()
        release = threading.Event()
        entered = threading.Event()
        def slow(*args):
            entered.set()
            release.wait(2)
            return {'results': []}
        try:
            with patch.object(studio, 'PUBLIC_DEADLINE', .05), patch.object(studio, 'nasa_media', side_effect=slow) as call:
                start = time.monotonic()
                with self.assertRaises(TimeoutError): service.request('nasa', {'query': 'moon'})
                self.assertTrue(entered.is_set())
                with self.assertRaises(RuntimeError): service.request('nasa', {'query': 'mars'})
                self.assertLess(time.monotonic() - start, .5)
                self.assertEqual(call.call_count, 1)
        finally:
            release.set()
        self.assertTrue(service.lock.acquire(timeout=2))
        service.lock.release()

if __name__ == '__main__': unittest.main(verbosity=2)
