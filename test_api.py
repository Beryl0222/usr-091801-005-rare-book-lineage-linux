"""HTTP 层冒烟测试：路由、状态码与确定性响应。"""

import json
import threading
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from domain import Store
from seed import build_seed
from service import Service


class ApiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.store, cls.ids = build_seed(Store())
        cls.server = Service(("127.0.0.1", 0), cls.store)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=2)

    def get(self, path):
        with urlopen(f"{self.base}{path}", timeout=2) as response:
            return response.status, json.load(response)

    def post(self, path, payload):
        request = Request(
            f"{self.base}{path}",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(request, timeout=2) as response:
            return response.status, json.load(response)

    def error(self, method, path, payload=None):
        with self.assertRaises(HTTPError) as ctx:
            if method == "GET":
                urlopen(f"{self.base}{path}", timeout=2)
            else:
                self.post(path, payload)
        return ctx.exception

    def test_health(self):
        status, body = self.get("/health")
        self.assertEqual(status, 200)
        self.assertEqual(body["service"], "rare-book-lineage")

    def test_list_and_get(self):
        _, works = self.get("/works")
        self.assertEqual(len(works["items"]), 4)
        _, claim = self.get(f"/claims/{self.ids['claims'][0]}")
        self.assertEqual(claim["status"], "adopted")
        self.assertEqual(claim["confidence"], "高")

    def test_unknown_route_404(self):
        self.assertEqual(self.error("GET", "/unknown").code, 404)
        self.assertEqual(self.error("GET", "/works/W9999").code, 404)

    def test_volume_lineage_endpoint(self):
        _, lineage = self.get(f"/volumes/{self.ids['volumes'][4]}/lineage")
        self.assertEqual(len(lineage["holdings"]), 2)

    def test_merged_institution_view(self):
        _, view = self.get(f"/institutions/{self.ids['institutions'][5]}")
        self.assertEqual(view["merged_into"], self.ids["institutions"][3])

    def test_export_check_blocks_itemwise(self):
        status, report = self.post("/exports/check", {
            "usage": "publication", "region": "WORLD", "resolution": "high",
            "as_of": "2026-09-19", "image_ids": self.ids["images"],
        })
        self.assertEqual(status, 200)
        by_image = {item["image_id"]: item for item in report["items"]}
        self.assertTrue(by_image[self.ids["images"][0]]["allowed"])
        self.assertIn("许可已过期", by_image[self.ids["images"][2]]["reasons"])
        self.assertEqual(by_image[self.ids["images"][5]]["reasons"], ["无许可记录"])

    def test_export_check_is_byte_identical_on_repeat(self):
        payload = {
            "usage": "exhibition", "region": "GB", "resolution": "medium",
            "as_of": "2026-09-19", "image_ids": self.ids["images"],
        }
        first = json.dumps(self.post("/exports/check", payload)[1], sort_keys=True)
        second = json.dumps(self.post("/exports/check", payload)[1], sort_keys=True)
        self.assertEqual(first, second)

    def test_audit_endpoint(self):
        _, report = self.get(
            f"/publications/{self.ids['publication']}/audit?as_of=2026-09-19"
        )
        self.assertEqual(report["summary"]["claims"], 3)
        self.assertEqual(report["summary"]["blocked"], 1)
        self.assertEqual(len(report["by_institution"]), 2)

    def test_audit_requires_as_of(self):
        self.assertEqual(
            self.error("GET", f"/publications/{self.ids['publication']}/audit").code, 400
        )

    def test_duplicate_volume_post_returns_existing(self):
        payload = {
            "edition_id": self.ids["editions"][0],
            "institution_id": self.ids["institutions"][1],
            "shelfmark": "BnF-Chinois-9999",
        }
        _, first = self.post("/volumes", payload)
        _, second = self.post("/volumes", payload)
        self.assertTrue(first["created"])
        self.assertFalse(second["created"])
        self.assertEqual(first["id"], second["id"])
        # 清理：本测试在共享库上新增了一卷，不影响其他用例（各用例只读或幂等）。

    def test_import_bundle_is_idempotent(self):
        from seed import BUNDLE

        _, first = self.post("/import", BUNDLE)
        _, second = self.post("/import", BUNDLE)
        self.assertFalse(any(item["created"] for item in second["volumes"]))
        self.assertEqual(
            [item["id"] for item in first["volumes"]],
            [item["id"] for item in second["volumes"]],
        )

    def test_invalid_claim_rejected(self):
        error = self.error("POST", "/claims", {
            "kind": "dating",
            "subject": {"type": "work", "id": self.ids["works"][0]},
            "statement": "无证据观点", "confidence": "高", "evidence": [], "author": "测试",
        })
        self.assertEqual(error.code, 400)


if __name__ == "__main__":
    unittest.main()
