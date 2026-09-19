"""领域规则测试：去重、状态机、冻结、阻断、谱系与确定性。"""

import json
import unittest

from domain import DomainError, Store
from seed import build_seed

AS_OF = "2026-09-19"
AUDIT = dict(usage="publication", region="WORLD", resolution="high", as_of=AS_OF)


def fresh():
    return build_seed(Store())


class ImportDedupTest(unittest.TestCase):
    def setUp(self):
        self.store, self.ids = fresh()

    def test_duplicate_volume_import_does_not_create_second_copy(self):
        from seed import BUNDLE

        before = len(self.store.entities["volume"])
        report = self.store.import_bundle(BUNDLE)
        self.assertEqual(len(self.store.entities["volume"]), before)
        self.assertTrue(all(not item["created"] for item in report["volumes"]))
        self.assertEqual(
            [item["id"] for item in report["volumes"]],
            self.ids["volumes"],
        )

    def test_duplicate_import_after_migration_still_dedups(self):
        # 残卷已从柏林国家图书馆迁至大英图书馆，按原馆藏信息重复导入仍命中同一卷。
        vid, created = self.store.add_volume(
            self.ids["editions"][2], self.ids["institutions"][4], "Berl-Sin-45"
        )
        self.assertFalse(created)
        self.assertEqual(vid, self.ids["volumes"][4])
        vid2, created2 = self.store.add_volume(
            self.ids["editions"][2], self.ids["institutions"][0], "Berl-Sin-45"
        )
        self.assertFalse(created2)
        self.assertEqual(vid2, self.ids["volumes"][4])

    def test_duplicate_image_import_dedups(self):
        gid, created = self.store.add_image(self.ids["volumes"][0], "叶一a")
        self.assertFalse(created)
        self.assertEqual(gid, self.ids["images"][0])


class ClaimLifecycleTest(unittest.TestCase):
    def setUp(self):
        self.store, self.ids = fresh()
        self.c1, self.c2, self.c3, self.c4, self.c5, self.c6, self.c7 = self.ids["claims"]

    def test_seeded_statuses(self):
        claims = self.store.entities["claim"]
        self.assertEqual(claims[self.c1]["status"], "adopted")
        self.assertEqual(claims[self.c2]["status"], "discussion")
        self.assertEqual(claims[self.c5]["status"], "discussion")
        self.assertEqual(claims[self.c6]["status"], "withdrawn")

    def test_withdrawn_is_terminal(self):
        with self.assertRaises(DomainError):
            self.store.transition_claim(self.c6, "discussion")

    def test_draft_cannot_jump_to_adopted(self):
        cid = self.store.add_claim(
            "dating", {"type": "work", "id": self.ids["works"][0]},
            "测试观点", "低", [{"image_id": self.ids["images"][0]}], "测试者",
        )
        with self.assertRaises(DomainError):
            self.store.transition_claim(cid, "adopted")

    def test_withdrawn_claim_keeps_evidence(self):
        claim = self.store.entities["claim"][self.c6]
        self.assertEqual(claim["status"], "withdrawn")
        self.assertEqual(len(claim["evidence"]), 1)
        self.assertEqual(claim["evidence"][0]["image_id"], self.ids["images"][4])

    def test_revision_chain(self):
        claims = self.store.entities["claim"]
        self.assertEqual(claims[self.c7]["version"], 2)
        self.assertEqual(claims[self.c7]["revises"], self.c3)
        self.assertEqual(claims[self.c3]["superseded_by"], self.c7)

    def test_re_revising_superseded_claim_rejected(self):
        with self.assertRaises(DomainError) as ctx:
            self.store.add_claim(
                "authorship", {"type": "work", "id": self.ids["works"][1]},
                "再次修订", "中", [{"image_id": self.ids["images"][2]}],
                "陈研", revises=self.c3,
            )
        self.assertEqual(ctx.exception.status, 409)

    def test_claim_requires_evidence_and_valid_confidence(self):
        with self.assertRaises(DomainError):
            self.store.add_claim(
                "dating", {"type": "work", "id": self.ids["works"][0]},
                "无证据", "高", [], "陈研",
            )
        with self.assertRaises(DomainError):
            self.store.add_claim(
                "dating", {"type": "work", "id": self.ids["works"][0]},
                "置信度非法", "也许", [{"image_id": self.ids["images"][0]}], "陈研",
            )
        with self.assertRaises(DomainError):
            self.store.add_claim(
                "dating", {"type": "work", "id": self.ids["works"][0]},
                "证据不存在", "高", [{"image_id": "G9999"}], "陈研",
            )

    def test_volume_leaf_evidence_without_image(self):
        cid = self.store.add_claim(
            "transmission", {"type": "volume", "id": self.ids["volumes"][2]},
            "卷册曾藏于江户某藩校", "低",
            [{"volume_id": self.ids["volumes"][2], "leaf": "叶九a"}], "林述",
        )
        claim = self.store.entities["claim"][cid]
        self.assertEqual(claim["evidence"][0]["leaf"], "叶九a")

    def test_comment_attached(self):
        comments = [
            c for c in self.store.entities["comment"].values()
            if c["claim_id"] == self.c2
        ]
        self.assertEqual(len(comments), 1)
        self.assertIn("崇祯说待考", comments[0]["body"])


class PublicationFreezeTest(unittest.TestCase):
    def setUp(self):
        self.store, self.ids = fresh()
        self.pub = self.store.entities["publication"][self.ids["publication"]]

    def test_freeze_requires_adopted(self):
        with self.assertRaises(DomainError) as ctx:
            self.store.create_publication("非法出版", [self.ids["claims"][1]])
        self.assertEqual(ctx.exception.status, 409)
        with self.assertRaises(DomainError):
            self.store.create_publication("撤回观点", [self.ids["claims"][5]])

    def test_snapshot_survives_rename_and_withdrawal(self):
        self.store.rename_institution(self.ids["institutions"][1], "法国国家图书馆（黎塞留馆）")
        self.store.transition_claim(self.ids["claims"][0], "withdrawn")
        snap = self.pub["items"][0]
        self.assertEqual(snap["status_at_freeze"], "adopted")
        self.assertEqual(snap["evidence"][0]["institution_name"], "法国国家图书馆")
        audit = self.store.audit_publication(self.ids["publication"], **AUDIT)
        item = audit["items"][0]
        self.assertEqual(item["current_status"], "withdrawn")
        self.assertEqual(item["evidence"][0]["current_institution_name"], "法国国家图书馆（黎塞留馆）")

    def test_audit_reports_adopted_version(self):
        audit = self.store.audit_publication(self.ids["publication"], **AUDIT)
        by_claim = {item["claim_id"]: item for item in audit["items"]}
        self.assertEqual(by_claim[self.ids["claims"][6]]["adopted_version"], 2)
        self.assertTrue(by_claim[self.ids["claims"][6]]["is_latest"])


class LicenseExportTest(unittest.TestCase):
    def setUp(self):
        self.store, self.ids = fresh()
        self.g = self.ids["images"]

    def check(self, image_ids, **overrides):
        params = dict(usage="publication", region="WORLD", resolution="high", as_of=AS_OF)
        params.update(overrides)
        return self.store.export_check(image_ids, **params)

    def test_allowed_when_within_license(self):
        result = self.check([self.g[0]])
        self.assertTrue(result["items"][0]["allowed"])

    def test_expired_license_blocks(self):
        result = self.check([self.g[2]], region="GB", resolution="medium")
        self.assertFalse(result["items"][0]["allowed"])
        self.assertIn("许可已过期", result["items"][0]["reasons"])

    def test_expiry_boundary_is_inclusive(self):
        self.assertTrue(self.check([self.g[0]], as_of="2028-12-31")["items"][0]["allowed"])
        self.assertFalse(self.check([self.g[0]], as_of="2029-01-01")["items"][0]["allowed"])

    def test_usage_region_resolution_block(self):
        result = self.check([self.g[3]])
        reasons = result["items"][0]["reasons"]
        self.assertEqual(
            reasons, ["用途超出许可范围", "地域超出许可范围", "清晰度超出许可范围"]
        )

    def test_unlicensed_image_blocked(self):
        result = self.check([self.g[5]])
        self.assertEqual(result["items"][0]["reasons"], ["无许可记录"])

    def test_missing_image_blocked_itemwise(self):
        result = self.check([self.g[0], "G9999"])
        self.assertTrue(result["items"][0]["allowed"])
        self.assertEqual(result["items"][1]["reasons"], ["图像不存在"])
        self.assertEqual(result["summary"], {"total": 2, "allowed": 1, "blocked": 1})

    def test_exhibition_export_uses_same_rules(self):
        result = self.check([self.g[0]], usage="exhibition")
        self.assertFalse(result["items"][0]["allowed"])  # BnF 许可不含展览用途


class LineageTest(unittest.TestCase):
    def setUp(self):
        self.store, self.ids = fresh()

    def test_volume_migration_preserves_lineage(self):
        lineage = self.store.volume_lineage(self.ids["volumes"][4])
        self.assertEqual(len(lineage["holdings"]), 2)
        self.assertEqual(lineage["holdings"][0]["institution_id"], self.ids["institutions"][4])
        self.assertEqual(lineage["holdings"][1]["institution_id"], self.ids["institutions"][0])
        view = self.store.view("volume", self.ids["volumes"][4])
        self.assertEqual(view["current_institution_name"], "大英图书馆")

    def test_migrating_to_current_holder_rejected(self):
        with self.assertRaises(DomainError) as ctx:
            self.store.migrate_volume(self.ids["volumes"][0], self.ids["institutions"][1])
        self.assertEqual(ctx.exception.status, 409)

    def test_institution_rename_keeps_history(self):
        self.store.rename_institution(self.ids["institutions"][1], "法国国家图书馆（黎塞留馆）")
        inst = self.store.view("institution", self.ids["institutions"][1])
        self.assertEqual(inst["name"], "法国国家图书馆（黎塞留馆）")
        self.assertEqual(inst["names"][0], "法国国家图书馆")
        self.assertEqual(len(inst["name_events"]), 2)

    def test_merge_suggestion_acceptance_aliasing(self):
        dup = self.ids["institutions"][5]
        self.assertEqual(self.store.resolve("institution", dup), self.ids["institutions"][3])
        view = self.store.view("institution", dup)
        self.assertEqual(view["merged_into"], self.ids["institutions"][3])
        merge = self.store.entities["merge"][self.ids["merge"]]
        self.assertEqual(merge["status"], "accepted")

    def test_merge_rejection_keeps_entities_separate(self):
        mid = self.store.add_merge(
            "work", self.ids["works"][0], self.ids["works"][1], "测试拒绝"
        )
        self.store.resolve_merge(mid, False)
        self.assertIsNone(self.store.merged_into("work", self.ids["works"][0]))
        with self.assertRaises(DomainError):
            self.store.resolve_merge(mid, True)  # 已处理的建议不能重复处理


class DeterminismTest(unittest.TestCase):
    def test_identical_inputs_produce_identical_state(self):
        store_a, ids_a = fresh()
        store_b, ids_b = fresh()
        self.assertEqual(ids_a, ids_b)
        dump = lambda s: json.dumps(s.dump(), ensure_ascii=False, sort_keys=True)
        self.assertEqual(dump(store_a), dump(store_b))

    def test_audit_and_export_are_reproducible(self):
        store, ids = fresh()
        audit1 = store.audit_publication(ids["publication"], **AUDIT)
        audit2 = store.audit_publication(ids["publication"], **AUDIT)
        self.assertEqual(
            json.dumps(audit1, sort_keys=True), json.dumps(audit2, sort_keys=True)
        )
        self.assertEqual(audit1["summary"]["blocked"], 1)  # BL 许可已于 2026-06-30 到期
        inst = {b["institution_id"]: b for b in audit1["by_institution"]}
        self.assertEqual(inst[ids["institutions"][0]]["blocked"], 1)
        self.assertEqual(inst[ids["institutions"][1]]["evidence"], 2)


if __name__ == "__main__":
    unittest.main()
