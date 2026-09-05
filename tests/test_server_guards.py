"""The studio's front door.

The studio binds to loopback, but loopback is not a boundary a browser
respects: any page the author happens to have open can POST to 127.0.0.1, and
a hostname rebound to 127.0.0.1 would read the replies as same-origin. These
tests pin the three guards that close that door, and the path checks that keep
a scene name from naming a file outside its own directory.
"""

import http.client
import json
import shutil
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path

from fabula import server as srv

from . import samples

SAMPLE = "Shadows of Memory"


class ServerCase(unittest.TestCase):
    """A real server on an ephemeral port, over a throwaway copy of the sample
    book — nothing here touches the author's own library."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="fabula-test-"))
        book_dir = cls.tmp / "books" / SAMPLE
        book_dir.mkdir(parents=True)
        shutil.copy(samples.BOOKS / SAMPLE / "book.fab", book_dir / "book.fab")

        cls._registry = srv.ProjectManager.REGISTRY
        srv.ProjectManager.REGISTRY = cls.tmp / "home" / "projects.json"
        srv.Handler.manager = srv.ProjectManager(book_dir / "book.fab", book_dir / "build")

        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), srv.Handler)
        cls.port = cls.httpd.server_address[1]
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.thread.join(timeout=5)
        srv.ProjectManager.REGISTRY = cls._registry
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def request(self, method, path, host=None, origin=None, body=None,
                content_type="application/json"):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        headers = {"Host": host if host is not None else f"127.0.0.1:{self.port}"}
        if origin is not None:
            headers["Origin"] = origin
        payload = None
        if body is not None:
            payload = json.dumps(body).encode("utf-8")
            if content_type:
                headers["Content-Type"] = content_type
        try:
            conn.request(method, path, body=payload, headers=headers)
            response = conn.getresponse()
            return response.status, response.read()
        finally:
            conn.close()

    def json_body(self, raw):
        return json.loads(raw.decode("utf-8"))


class SameOriginGuard(ServerCase):
    def test_the_studio_itself_is_served(self):
        status, raw = self.request("GET", "/")
        self.assertEqual(status, 200)
        self.assertIn(b"<html", raw.lower())

    def test_own_api_call_succeeds(self):
        status, raw = self.request("GET", "/api/project", origin=f"http://127.0.0.1:{self.port}")
        self.assertEqual(status, 200)
        self.assertIn("providers", self.json_body(raw))

    def test_a_rebound_hostname_is_refused(self):
        """DNS rebinding: the request really does arrive on 127.0.0.1, but the
        browser thinks it is talking to evil.example and would let the page
        read the reply."""
        for host in ("evil.example", "evil.example:80", f"attacker.test:{self.port}"):
            with self.subTest(host=host):
                status, _ = self.request("GET", "/api/project", host=host)
                self.assertEqual(status, 403)

    def test_a_cross_site_origin_is_refused(self):
        for origin in ("http://evil.example", "https://evil.example",
                       "http://127.0.0.1:1", "null"):
            with self.subTest(origin=origin):
                status, _ = self.request("GET", "/api/project", origin=origin)
                self.assertEqual(status, 403)

    def test_a_cross_site_write_is_refused_before_it_reaches_the_project(self):
        status, _ = self.request("POST", "/api/source", origin="http://evil.example",
                                 body={"source": 'book "Pwned" {}'})
        self.assertEqual(status, 403)
        self.assertNotIn("Pwned", self.manager_source())

    def test_a_form_post_is_refused_on_content_type(self):
        """A cross-site <form> cannot send application/json, and anything else
        must not be accepted — that is what forces a preflight this server
        never answers."""
        for ctype in ("text/plain", "application/x-www-form-urlencoded",
                      "multipart/form-data"):
            with self.subTest(content_type=ctype):
                status, _ = self.request("POST", "/api/source",
                                         body={"source": 'book "Pwned" {}'},
                                         content_type=ctype)
                self.assertEqual(status, 415)
        self.assertNotIn("Pwned", self.manager_source())

    def test_a_json_post_from_the_studio_itself_works(self):
        status, raw = self.request("POST", "/api/compile",
                                   origin=f"http://localhost:{self.port}",
                                   body={"source": 'book "Draft" { language: "en"; }'})
        self.assertEqual(status, 200)
        self.assertEqual(self.json_body(raw)["book"]["title"], "Draft")

    def test_localhost_and_ipv6_loopback_count_as_the_studio(self):
        for host in (f"localhost:{self.port}", f"[::1]:{self.port}"):
            with self.subTest(host=host):
                status, _ = self.request("GET", "/api/project", host=host)
                self.assertEqual(status, 200)

    def test_the_right_name_on_the_wrong_port_is_refused(self):
        """Another local service on another port is a different origin."""
        status, _ = self.request("GET", "/api/project", host="127.0.0.1:9")
        self.assertEqual(status, 403)

    def manager_source(self) -> str:
        return srv.Handler.manager.current.read_source()


class PathGuards(ServerCase):
    def test_a_scene_or_file_name_cannot_carry_path_syntax(self):
        hostile = ("../../../../etc/passwd", "..\\..\\windows\\win.ini", "a/b")
        for name in hostile:
            with self.subTest(name=name):
                status, _ = self.request(
                    "GET", f"/api/variant?scene={name}&file=v1.md")
                self.assertEqual(status, 400)
                status, _ = self.request(
                    "GET", f"/api/variant?scene=The%20Artefact&file={name}")
                self.assertEqual(status, 400)

    def test_write_endpoints_reject_the_same_names(self):
        for path in ("/api/revise", "/api/variant/save"):
            with self.subTest(endpoint=path):
                status, _ = self.request("POST", path, body={
                    "scene": "../../etc", "file": "../../../etc/passwd", "text": "x"})
                self.assertEqual(status, 400)

    def test_a_locale_code_cannot_escape_the_locales_directory(self):
        status, raw = self.request("GET", "/api/i18n?lang=../../../../etc/passwd")
        self.assertEqual(status, 200)
        self.assertEqual(self.json_body(raw)["strings"]["_lang"], "en")

    def test_unknown_paths_are_not_served_off_disk(self):
        for path in ("/../pyproject.toml", "/nope", "/api/nope"):
            with self.subTest(path=path):
                status, _ = self.request("GET", path)
                self.assertEqual(status, 404)


class Reporting(ServerCase):
    def test_compile_errors_come_back_as_diagnostics_not_a_crash(self):
        status, raw = self.request("POST", "/api/compile",
                                   body={"source": 'book "T" { character "Ann" { '})
        self.assertEqual(status, 200)
        payload = self.json_body(raw)
        self.assertFalse(payload["ok"])
        self.assertTrue(payload["diagnostics"][0]["code"].startswith("FAB"))


if __name__ == "__main__":
    unittest.main()
