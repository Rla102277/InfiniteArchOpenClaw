import http.client
import json
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from aihq.server import make_handler


class APIIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), make_handler('test-secret'))
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def request(self, method, route, body=None, token=None, host=None):
        con = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=4)
        headers = {'Host': host or f'127.0.0.1:{self.server.server_port}'}
        if body is not None:
            body = json.dumps(body)
            headers['Content-Type'] = 'application/json'
        if token:
            headers['X-HQ-Token'] = token
        con.request(method, route, body=body, headers=headers)
        response = con.getresponse()
        status = response.status
        payload = response.read()
        con.close()
        return status, json.loads(payload)

    def test_loopback_state_read(self):
        status, data = self.request('GET', '/api/state')
        self.assertEqual(status, 200)
        self.assertIn('tools', data)
        self.assertIn('skills', data)
        self.assertNotIn('projects', data)

    def test_legacy_project_routes_are_gone(self):
        self.assertEqual(self.request('POST', '/api/projects', {'name': 'x', 'idea': 'y'}, 'test-secret')[0], 404)

    def test_csrf_and_host_rejection(self):
        self.assertEqual(self.request('POST', '/api/os/action', {'action': 'reconcile'})[0], 403)
        self.assertEqual(self.request('GET', '/api/state', host='evil.example')[0], 403)

    def test_unknown_route(self):
        self.assertEqual(self.request('GET', '/bad')[0], 404)


if __name__ == '__main__':
    unittest.main()
