"""领域行为测试：覆盖协作、证据、许可、谱系与确定性需求。"""

import json
import os
import unittest

from lineage import ops
from lineage.audit import audit_entries, audit_publication
from lineage.model import DomainError
from lineage.store import Store

SEED_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         "data", "seed.json")
AS_OF = "2026-09-19"


def load_seed():
    with open(SEED_PATH, encoding="utf-8") as fh:
        return json.load(fh)


def seeded_store():
    store = Store()
    ops.ingest(store, load_seed())
    return store


def only(store, kind, **criteria):
    matches = [r for r in store.bucket(kind).values()
               if all(r.get(k) == v for k, v in criteria.items())]
    if len(matches) != 1:
        raise AssertionError(f"{kind} 匹配到 {len(matches)} 条: {criteria}")
    return matches[0]


def dump(obj):
    return json.dumps(obj, ensure_ascii=False, sort_keys=True)


class SeedDataTest(unittest.TestCase):
    def test_bibliography_and_licenses_are_queryable_data(self):
        store = seeded_store()
        self.assertEqual(len(store.bucket("work")), 4)
        self.assertEqual(len(store.bucket("license")), 4)
        wsp = only(store, "work", title="无双谱")
        self.assertIn("南陵无双谱", wsp["aliases"])
        bnf = only(store, "license", title="BnF 研究许可")
        self.assertEqual(bnf["expires_on"], "2026-06-30")

    def test_ingest_is_idempotent_no_duplicate_volumes(self):
        store = Store()
        first = ops.ingest(store, load_seed())
        snapshot = dump(store.to_dict())
        second = ops.ingest(store, load_seed())
        self.assertEqual(dump(store.to_dict()), snapshot)
        self.assertEqual(len(store.bucket("volume")), 4)
        self.assertFalse(second["created"])
        self.assertIn("VOL-0001", first["created"]["volume"])


class ClaimLifecycleTest(unittest.TestCase):
    def setUp(self):
        self.store = seeded_store()
        self.edition = only(self.store, "edition", label="清康熙刻本（馆藏著录）")
        self.image = only(self.store, "image", leaf="卷首·叶一·a")

    def _draft_dating(self, statement, author="研究者戊"):
        return ops.add_claim(self.store, "dating", "edition", self.edition["id"],
                             statement, "medium", author)

    def test_adopt_requires_locatable_evidence(self):
        claim = self._draft_dating("刊刻于康熙四十年")
        ops.transition_claim(self.store, claim["id"], "discussion")
        with self.assertRaises(DomainError):
            ops.transition_claim(self.store, claim["id"], "adopted")
        ops.add_evidence(self.store, claim["id"], image_id=self.image["id"],
                         note="牌记", confidence="medium")
        ops.transition_claim(self.store, claim["id"], "adopted")
        self.assertEqual(claim["status"], "adopted")

    def test_evidence_must_be_locatable(self):
        claim = self._draft_dating("刊刻于康熙四十年")
        with self.assertRaises(DomainError):
            ops.add_evidence(self.store, claim["id"], note="既无图像也无卷册页叶")
        with self.assertRaises(KeyError):
            ops.add_evidence(self.store, claim["id"], image_id="IMG-9999")
        ev = ops.add_evidence(self.store, claim["id"],
                              volume_id=only(self.store, "volume",
                                             shelfmark="15333.d.4")["id"],
                              leaf="卷首·叶九·a")
        self.assertEqual(ev["leaf"], "卷首·叶九·a")

    def test_competing_claims_coexist_and_adoption_demotes(self):
        adopted = only(self.store, "claim", statement="刊刻于清康熙三十三年（1694）")
        rival = self._draft_dating("刊刻于康熙四十年前后")
        ops.add_evidence(self.store, rival["id"], image_id=self.image["id"])
        ops.transition_claim(self.store, rival["id"], "discussion")
        ops.transition_claim(self.store, rival["id"], "adopted")
        self.assertEqual(rival["status"], "adopted")
        self.assertEqual(adopted["status"], "discussion")
        # 竞争观点都仍在库中，可并存查询
        dating = [c for c in self.store.bucket("claim").values()
                  if c["kind"] == "dating"
                  and c["subject"]["id"] == self.edition["id"]]
        self.assertEqual(len(dating), 3)

    def test_revision_chain_via_supersedes(self):
        old = only(self.store, "claim", statement="刊刻于清康熙三十三年（1694）")
        revised = ops.add_claim(self.store, "dating", "edition",
                                self.edition["id"], "刊刻于康熙三十三年至三十五年间",
                                "medium", "研究者甲", supersedes=old["id"])
        self.assertEqual(revised["supersedes"], old["id"])

    def test_retract_keeps_claim_and_evidence(self):
        claim = only(self.store, "claim", statement="金古良（金史）辑绘")
        evidence_ids = [e["id"] for e in ops.evidence_of(self.store, claim["id"])]
        ops.transition_claim(self.store, claim["id"], "retracted")
        self.assertEqual(claim["status"], "retracted")
        self.assertEqual(
            [e["id"] for e in ops.evidence_of(self.store, claim["id"])],
            evidence_ids,
        )
        with self.assertRaises(DomainError):
            ops.transition_claim(self.store, claim["id"], "discussion")

    def test_invalid_transition_rejected(self):
        claim = self._draft_dating("刊刻于康熙四十年")
        with self.assertRaises(DomainError):
            ops.transition_claim(self.store, claim["id"], "adopted")


class LicenseAndExportTest(unittest.TestCase):
    def setUp(self):
        self.store = seeded_store()

    def test_each_license_dimension_blocks(self):
        bnf_image = only(self.store, "image", leaf="卷一·叶三·a")
        cases = [
            # 用途越权
            ("exhibition", "FR", "high", "2026-01-01", "用途"),
            # 地域越权
            ("research", "CN", "high", "2026-01-01", "地域"),
            # 清晰度越权（柏林许可上限 low）
            ("research", "DE", "medium", "2026-01-01", "清晰度"),
            # 许可到期
            ("research", "FR", "high", "2026-07-01", "到期"),
        ]
        for purpose, region, resolution, as_of, keyword in cases:
            image = bnf_image
            if keyword == "清晰度":
                image = only(self.store, "image", leaf="卷二·叶一·a")
            result = ops.build_export(self.store, [image["id"]],
                                      purpose, region, resolution, as_of)
            self.assertEqual(result["allowed"], [])
            self.assertEqual(len(result["blocked"]), 1)
            self.assertIn(keyword, result["blocked"][0]["reasons"][0])

    def test_unlicensed_image_blocked(self):
        volume = only(self.store, "volume", shelfmark="15333.d.4")
        image = ops.create_image(self.store, volume["id"], "卷一·叶一·a")
        result = ops.build_export(self.store, [image["id"]],
                                  "research", "GB", "low", AS_OF)
        self.assertEqual(result["blocked"][0]["reasons"], ["图像未关联许可"])

    def test_export_blocks_item_by_item(self):
        image_ids = [i["id"] for i in self.store.bucket("image").values()]
        result = ops.build_export(self.store, image_ids,
                                  "exhibition", "EU", "high", AS_OF)
        naid = only(self.store, "image", leaf="卷一·叶五·b")
        self.assertEqual(result["allowed"], [naid["id"]])
        self.assertEqual(len(result["blocked"]), 4)
        self.assertEqual(result["blocked"],
                         sorted(result["blocked"], key=lambda b: b["image_id"]))

    def test_export_is_deterministic(self):
        image_ids = [i["id"] for i in self.store.bucket("image").values()]
        first = ops.build_export(self.store, image_ids,
                                 "exhibition", "EU", "high", AS_OF)
        second = ops.build_export(self.store, list(reversed(image_ids)),
                                  "exhibition", "EU", "high", AS_OF)
        self.assertEqual(dump(first), dump(second))


class PublicationFreezeTest(unittest.TestCase):
    def setUp(self):
        self.store = seeded_store()

    def test_freeze_requires_adopted(self):
        draft = ops.add_claim(self.store, "dating", "work",
                              only(self.store, "work", title="无双谱")["id"],
                              "某说", "low", "研究者戊")
        with self.assertRaises(DomainError):
            ops.freeze_publication(self.store, "测试图录", "research", "CN",
                                   "low", [draft["id"]])

    def test_frozen_snapshot_survives_retraction(self):
        pub = only(self.store, "publication", title="江南版画展览图录（样例）")
        item = next(i for i in pub["items"]
                    if i["claim"]["statement"] == "刊刻于清康熙三十三年（1694）")
        self.assertEqual(item["status_at_freeze"], "adopted")
        claim = self.store.get("claim", item["claim_id"])
        ops.transition_claim(self.store, claim["id"], "retracted")
        # 快照不变，历史引用保留
        self.assertEqual(item["claim"]["statement"], "刊刻于清康熙三十三年（1694）")
        self.assertEqual(item["status_at_freeze"], "adopted")
        self.assertTrue(item["evidence"])
        report = audit_publication(self.store, pub["id"], AS_OF)
        entry = next(e for e in report["entries"]
                     if e["claim_id"] == claim["id"])
        self.assertEqual(entry["claim_status"], "retracted")
        self.assertFalse(entry["still_adopted"])
        self.assertEqual(entry["verdict"], "fail")

    def test_audit_flags_superseded_adoption(self):
        pub = only(self.store, "publication", title="江南版画展览图录（样例）")
        item = next(i for i in pub["items"]
                    if i["claim"]["statement"] == "刊刻于清康熙三十三年（1694）")
        claim = self.store.get("claim", item["claim_id"])
        rival = ops.add_claim(self.store, "dating", "edition",
                              claim["subject"]["id"], "刊刻于康熙四十年前后",
                              "medium", "研究者戊")
        image = only(self.store, "image", leaf="卷首·叶一·a")
        ops.add_evidence(self.store, rival["id"], image_id=image["id"])
        ops.transition_claim(self.store, rival["id"], "discussion")
        ops.transition_claim(self.store, rival["id"], "adopted")
        report = audit_publication(self.store, pub["id"], AS_OF)
        entry = next(e for e in report["entries"]
                     if e["claim_id"] == claim["id"])
        self.assertFalse(entry["still_adopted"])
        self.assertEqual(entry["superseded_by"], rival["id"])
        self.assertEqual(entry["verdict"], "fail")


class AuditTest(unittest.TestCase):
    def setUp(self):
        self.store = seeded_store()

    def test_seed_publication_audit_mixed_verdicts(self):
        pub = only(self.store, "publication", title="江南版画展览图录（样例）")
        report = audit_publication(self.store, pub["id"], AS_OF)
        self.assertEqual(report["summary"]["total"], 3)
        self.assertEqual(report["summary"]["passed"], 1)
        self.assertEqual(report["summary"]["failed"], 2)
        self.assertEqual(report["summary"]["additional_images_blocked"], 1)
        by_statement = {}
        for entry in report["entries"]:
            claim = self.store.get("claim", entry["claim_id"])
            by_statement[claim["statement"]] = entry
        # 内阁文库图像授权充分 → 通过
        self.assertEqual(by_statement["江户时期经长崎输入，后归红叶山文库"]["verdict"],
                         "pass")
        # 大英许可不含展览用途 → 阻断
        wsp = by_statement["刊刻于清康熙三十三年（1694）"]
        self.assertEqual(wsp["verdict"], "fail")
        self.assertIn("用途", wsp["images"][0]["reasons"][0])
        # BnF 许可用途越权且已到期 → 两条阻断理由
        hsgj = by_statement["彩色套印（饾版）"]
        reasons = hsgj["images"][0]["reasons"]
        self.assertEqual(len(reasons), 2)
        self.assertTrue(any("用途" in r for r in reasons))
        self.assertTrue(any("到期" in r for r in reasons))

    def test_audit_is_byte_identical_across_runs(self):
        pub = only(self.store, "publication", title="江南版画展览图录（样例）")
        first = dump(audit_publication(self.store, pub["id"], AS_OF))
        second = dump(audit_publication(self.store, pub["id"], AS_OF))
        self.assertEqual(first, second)
        # 同样的操作序列重建库，审计结果仍一致
        rebuilt = seeded_store()
        third = dump(audit_publication(rebuilt, pub["id"], AS_OF))
        self.assertEqual(first, third)

    def test_audit_entries_checklist(self):
        claim = only(self.store, "claim", statement="刊于明万历三十七年（1609）")
        image = only(self.store, "image", leaf="卷二·叶一·a")
        report = audit_entries(
            self.store,
            [{"claim_id": claim["id"], "image_ids": [image["id"]]}],
            "research", "DE", "low", AS_OF,
        )
        self.assertEqual(report["entries"][0]["verdict"], "pass")
        report = audit_entries(
            self.store,
            [{"claim_id": claim["id"], "image_ids": [image["id"]]}],
            "research", "DE", "high", AS_OF,
        )
        self.assertEqual(report["entries"][0]["verdict"], "fail")

    def test_audit_requires_as_of(self):
        pub = only(self.store, "publication", title="江南版画展览图录（样例）")
        with self.assertRaises(DomainError):
            audit_publication(self.store, pub["id"], None)


class LineageTest(unittest.TestCase):
    def setUp(self):
        self.store = seeded_store()

    def test_rename_institution_keeps_lineage(self):
        inst = only(self.store, "institution", name="法国国家图书馆")
        volume = only(self.store, "volume", shelfmark="CHINOIS 6274")
        ops.rename_institution(self.store, inst["id"], "法国国家图书馆（黎塞留馆）")
        self.assertEqual(inst["id"], volume["institution_id"])
        self.assertEqual(inst["name_history"][0]["name"], "法国国家图书馆")
        events = [e for e in self.store.events if e["type"] == "institution.renamed"]
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["institution_id"], inst["id"])

    def test_transfer_volume_keeps_lineage(self):
        volume = only(self.store, "volume", shelfmark="Libri sin. 1234")
        bnf = only(self.store, "institution", name="法国国家图书馆")
        claim = only(self.store, "claim", statement="刊于明万历三十七年（1609）")
        evidence_before = ops.evidence_of(self.store, claim["id"])
        ops.transfer_volume(self.store, volume["id"], bnf["id"],
                            shelfmark="CHINOIS 9999")
        self.assertEqual(volume["institution_id"], bnf["id"])
        self.assertEqual(ops.evidence_of(self.store, claim["id"]), evidence_before)
        events = [e for e in self.store.events if e["type"] == "volume.transferred"]
        self.assertEqual(events[0]["volume_id"], volume["id"])
        self.assertEqual(events[0]["from_institution_id"],
                         only(self.store, "institution",
                              name="柏林国家图书馆")["id"])

    def test_merge_works_preserves_lineage(self):
        store = self.store
        dup = ops.create_work(store, "無雙譜", aliases=["无双谱（重出）"])
        orphan_edition = ops.create_edition(store, dup["id"], "清翻刻本（待考）")
        claim = ops.add_claim(store, "dating", "edition", orphan_edition["id"],
                              "翻刻不早于雍正", "low", "研究者己")
        survivor = only(store, "work", title="无双谱")
        suggestion = ops.suggest_merge(store, "work", survivor["id"], dup["id"],
                                       "同一书之重出条目", "研究者甲")
        ops.resolve_merge(store, suggestion["id"], True, actor="研究者乙")
        # 版本改挂存续作品，异名合并，墓碑可解析
        self.assertEqual(orphan_edition["work_id"], survivor["id"])
        self.assertIn("無雙譜", survivor["aliases"])
        self.assertEqual(store.get("work", dup["id"])["id"], survivor["id"])
        # 挂在被合并实体上的观点仍然可定位
        self.assertEqual(store.get("edition", claim["subject"]["id"])["work_id"],
                         survivor["id"])
        events = [e for e in store.events if e["type"] == "merge.accepted"]
        self.assertEqual(events[0]["absorbed_id"], dup["id"])

    def test_merge_volumes_moves_images(self):
        store = self.store
        bnf = only(store, "institution", name="法国国家图书馆")
        dup = ops.create_volume(store, bnf["id"], "CHINOIS 6274 bis",
                                completeness="残卷", juan_held=["卷二"])
        image = ops.create_image(store, dup["id"], "卷二·叶一·a")
        survivor = only(store, "volume", shelfmark="CHINOIS 6274")
        suggestion = ops.suggest_merge(store, "volume", survivor["id"], dup["id"],
                                       "同册重复著录", "研究者丙")
        ops.resolve_merge(store, suggestion["id"], True, actor="研究者甲")
        self.assertEqual(image["volume_id"], survivor["id"])
        self.assertIn("卷二", survivor["juan_held"])
        self.assertEqual(store.get("volume", dup["id"])["id"], survivor["id"])

    def test_merge_rejection_keeps_both(self):
        store = self.store
        dup = ops.create_work(store, "无双谱别本")
        survivor = only(store, "work", title="无双谱")
        suggestion = ops.suggest_merge(store, "work", survivor["id"], dup["id"],
                                       "疑似重出", "研究者甲")
        ops.resolve_merge(store, suggestion["id"], False, actor="研究者乙")
        self.assertIsNone(dup.get("merged_into"))
        self.assertEqual(suggestion["status"], "rejected")


class DeterminismTest(unittest.TestCase):
    def test_same_operations_produce_identical_state(self):
        self.assertEqual(dump(seeded_store().to_dict()),
                         dump(seeded_store().to_dict()))

    def test_save_load_roundtrip(self):
        import tempfile

        store = seeded_store()
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "data.json")
            store.save(path)
            loaded = Store.load(path)
        self.assertEqual(dump(store.to_dict()), dump(loaded.to_dict()))


if __name__ == "__main__":
    unittest.main()
