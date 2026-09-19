"""HTTP 接口测试：查询、协作操作、导出与审计端点。"""

import json
import threading
import unittest
from http.server import ThreadingHTTPServer
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from lineage.api import Handler
from test_lineage import AS_OF, load_seed, seeded_store


class ApiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        class TestHandler(Handler):
            store = None
            data_path = None

        cls.handler_class = TestHandler
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), TestHandler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base_url = f"http://127.0.0.1:{cls.server.server_port}"

    def setUp(self):
        # 每个用例从同样的样例数据重建，保证顺序无关、结果确定
        self.handler_class.store = seeded_store()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=2)

    def get(self, path, **params):
        if params:
            path = f"{path}?{urlencode(params)}"
        with urlopen(f"{self.base_url}{path}", timeout=2) as response:
            self.assertEqual(response.headers.get_content_type(), "application/json")
            return json.load(response)

    def post(self, path, payload):
        request = Request(
            f"{self.base_url}{path}",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(request, timeout=2) as response:
            return json.load(response)

    def post_error(self, path, payload):
        request = Request(
            f"{self.base_url}{path}",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with self.assertRaises(HTTPError) as error:
            urlopen(request, timeout=2)
        return error.exception

    # ------------------------------------------------------------ 查询

    def test_health(self):
        self.assertEqual(self.get("/health")["service"], "rare-book-lineage")

    def test_bibliography_queryable(self):
        works = self.get("/api/works")["works"]
        self.assertEqual(len(works), 4)
        wsp = next(w for w in works if w["title"] == "无双谱")
        detail = self.get(f"/api/works/{wsp['id']}")
        self.assertIn("南陵无双谱", detail["aliases"])
        self.assertEqual(len(detail["editions"]), 1)
        self.assertTrue(any(c["kind"] == "authorship" for c in detail["claims"]))

    def test_licenses_queryable(self):
        licenses = self.get("/api/licenses")["licenses"]
        self.assertEqual(len(licenses), 4)
        bnf = next(l for l in licenses if l["title"] == "BnF 研究许可")
        detail = self.get(f"/api/licenses/{bnf['id']}")
        self.assertEqual(detail["institution"]["name"], "法国国家图书馆")

    def test_claims_filter(self):
        adopted_dating = self.get("/api/claims", kind="dating", status="adopted")
        self.assertEqual(len(adopted_dating["claims"]), 2)
        volume = self.get("/api/volumes")["volumes"][0]
        claims = self.get("/api/claims", subject_type="volume",
                          subject_id=volume["id"])
        for claim in claims["claims"]:
            self.assertEqual(claim["subject"]["type"], "volume")

    def test_claim_detail_has_evidence_and_comments(self):
        claims = self.get("/api/claims", status="discussion")["claims"]
        rival = next(c for c in claims if "翻刻" in c["statement"])
        detail = self.get(f"/api/claims/{rival['id']}")
        self.assertEqual(detail["evidence"][0]["leaf"], "卷首·叶一·a")
        self.assertEqual(detail["comments"][0]["author"], "研究者丙")

    def test_events_exposed(self):
        events = self.get("/api/events")["events"]
        self.assertTrue(any(e["type"] == "publication.frozen" for e in events))

    def test_unknown_routes_404(self):
        for path in ("/unknown", "/api/works/W-9999", "/api/nope"):
            with self.assertRaises(HTTPError) as error:
                urlopen(f"{self.base_url}{path}", timeout=2)
            self.assertEqual(error.exception.code, 404)
            error.exception.close()

    # ------------------------------------------------------------ 协作操作

    def test_claim_workflow_over_http(self):
        edition = self.get("/api/editions")["editions"][0]
        image = self.get("/api/images")["images"][0]
        claim = self.post("/api/claims", {
            "kind": "dating",
            "subject_type": "edition",
            "subject_id": edition["id"],
            "statement": "刊刻年代待考",
            "confidence": "low",
            "author": "研究者庚",
        })
        self.assertEqual(claim["status"], "draft")
        # 未挂证据不能采用
        self.post(f"/api/claims/{claim['id']}/status", {"status": "discussion"})
        error = self.post_error(f"/api/claims/{claim['id']}/status",
                                {"status": "adopted"})
        self.assertEqual(error.code, 400)
        error.close()
        self.post(f"/api/claims/{claim['id']}/evidence",
                  {"image_id": image["id"], "note": "牌记照片"})
        self.post(f"/api/claims/{claim['id']}/comments",
                  {"author": "研究者辛", "body": "证据已补，同意采用。"})
        adopted = self.post(f"/api/claims/{claim['id']}/status",
                            {"status": "adopted", "actor": "研究者辛"})
        self.assertEqual(adopted["status"], "adopted")

    def test_rename_and_transfer_over_http(self):
        institutions = self.get("/api/institutions")["institutions"]
        inst, other = institutions[0], institutions[1]
        renamed = self.post(f"/api/institutions/{inst['id']}/rename",
                            {"name": inst["name"] + "（新馆）"})
        self.assertEqual(renamed["name_history"][0]["name"], inst["name"])
        volume = self.get("/api/volumes")["volumes"][0]
        moved = self.post(f"/api/volumes/{volume['id']}/transfer",
                          {"institution_id": other["id"]})
        self.assertEqual(moved["institution_id"], other["id"])

    def test_ingest_idempotent_over_http(self):
        before = len(self.get("/api/volumes")["volumes"])
        report = self.post("/api/ingest", load_seed())
        self.assertEqual(len(self.get("/api/volumes")["volumes"]), before)
        self.assertFalse(report["created"])

    def test_export_and_audit_over_http(self):
        images = self.get("/api/images")["images"]
        result = self.post("/api/exports/evaluate", {
            "image_ids": [i["id"] for i in images],
            "purpose": "exhibition",
            "region": "EU",
            "resolution": "high",
            "as_of": AS_OF,
        })
        self.assertEqual(len(result["allowed"]), 1)
        self.assertEqual(len(result["blocked"]), 4)
        pub = self.get("/api/publications")["publications"][0]
        report = self.post("/api/audits", {"publication_id": pub["id"],
                                           "as_of": AS_OF})
        self.assertEqual(report["summary"], {
            "total": 3, "passed": 1, "failed": 2,
            "additional_images_blocked": 1,
        })
        again = self.post("/api/audits", {"publication_id": pub["id"],
                                          "as_of": AS_OF})
        self.assertEqual(json.dumps(report, sort_keys=True, ensure_ascii=False),
                         json.dumps(again, sort_keys=True, ensure_ascii=False))

    def test_bad_requests_400(self):
        error = self.post_error("/api/works", {"aliases": ["缺题名"]})
        self.assertEqual(error.code, 400)
        error.close()
        error = self.post_error("/api/exports/evaluate", {
            "image_ids": ["IMG-0001"], "purpose": "research",
            "region": "CN", "resolution": "high",
        })
        self.assertEqual(error.code, 400)
        error.close()


if __name__ == "__main__":
    unittest.main()
