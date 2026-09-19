"""跨机构出版清单反向抽查：采用版本、页叶证据与授权状态。

同样输入重复生成完全一致的结果：条目按编号排序，输出 JSON 键序固定，
评估日期 as_of 由调用方显式给出，不读取系统时钟。
"""

from .model import DomainError, require
from .ops import SNAPSHOT_FIELDS, evidence_of, same_subject
from .policy import image_block_reasons, validate_request


def _current_adopted(store, claim):
    for other in store.bucket("claim").values():
        if (
            other["status"] == "adopted"
            and other["kind"] == claim["kind"]
            and same_subject(store, other["subject"], claim["subject"])
        ):
            return other
    return None


def _audit_claim(store, claim_id, image_ids, purpose, region, resolution, as_of,
                 frozen_claim=None, frozen_evidence_ids=None):
    claim = store.get("claim", claim_id)
    still_adopted = claim["status"] == "adopted"
    adopted_now = _current_adopted(store, claim)
    superseded_by = None
    if not still_adopted and adopted_now is not None and adopted_now["id"] != claim["id"]:
        superseded_by = adopted_now["id"]

    current_evidence = evidence_of(store, claim_id)
    if frozen_evidence_ids is not None:
        evidence_ids = sorted(frozen_evidence_ids)
        evidence_changed = evidence_ids != [e["id"] for e in current_evidence]
    else:
        evidence_ids = [e["id"] for e in current_evidence]
        evidence_changed = False

    evidence_results = []
    all_image_ids = set(image_ids)
    for eid in evidence_ids:
        ev = store.bucket("evidence").get(eid)
        resolves = True
        current_institution_id = None
        image_id = None
        leaf = None
        if ev is None:
            resolves = False
        else:
            image_id = ev.get("image_id")
            leaf = ev["leaf"]
            try:
                if image_id:
                    image = store.get("image", image_id)
                    volume = store.get("volume", image["volume_id"])
                    current_institution_id = volume["institution_id"]
                    all_image_ids.add(image_id)
                elif ev.get("volume_id"):
                    volume = store.get("volume", ev["volume_id"])
                    current_institution_id = volume["institution_id"]
                else:
                    resolves = False
            except (KeyError, DomainError):
                resolves = False
        evidence_results.append({
            "evidence_id": eid,
            "leaf": leaf,
            "image_id": image_id,
            "resolves": resolves,
            "current_institution_id": current_institution_id,
        })

    image_results = []
    for iid in sorted(all_image_ids):
        try:
            image = store.get("image", iid)
            reasons = image_block_reasons(store, image, purpose, region, resolution, as_of)
        except KeyError:
            reasons = [f"图像不存在: {iid}"]
        image_results.append({
            "image_id": iid,
            "authorized": not reasons,
            "reasons": reasons,
        })

    snapshot_matches = True
    if frozen_claim is not None:
        snapshot_matches = all(claim.get(k) == frozen_claim.get(k) for k in SNAPSHOT_FIELDS)

    ok = (
        still_adopted
        and snapshot_matches
        and not evidence_changed
        and all(e["resolves"] for e in evidence_results)
        and all(i["authorized"] for i in image_results)
    )
    return {
        "claim_id": claim_id,
        "claim_status": claim["status"],
        "still_adopted": still_adopted,
        "superseded_by": superseded_by,
        "snapshot_matches_current": snapshot_matches,
        "evidence_changed": evidence_changed,
        "evidence": evidence_results,
        "images": image_results,
        "verdict": "pass" if ok else "fail",
    }


def _report(store, entries, purpose, region, resolution, as_of, publication=None):
    validate_request(purpose, region, resolution, as_of)
    results = []
    for entry in entries:
        results.append(_audit_claim(
            store, entry["claim_id"], entry.get("image_ids") or [],
            purpose, region, resolution, as_of,
            frozen_claim=entry.get("frozen_claim"),
            frozen_evidence_ids=entry.get("frozen_evidence_ids"),
        ))
    results.sort(key=lambda r: r["claim_id"])
    passed = sum(1 for r in results if r["verdict"] == "pass")
    report = {
        "as_of": as_of,
        "purpose": purpose,
        "region": region,
        "resolution": resolution,
        "entries": results,
        "summary": {
            "total": len(results),
            "passed": passed,
            "failed": len(results) - passed,
        },
    }
    if publication is not None:
        report["publication_id"] = publication["id"]
        report["title"] = publication["title"]
    return report


def audit_entries(store, entries, purpose, region, resolution, as_of):
    """按外来出版清单逐条抽查。entries: [{"claim_id", "image_ids"?}]。"""
    require(isinstance(entries, list) and entries, "抽查清单不能为空")
    cleaned = []
    for entry in entries:
        require(isinstance(entry, dict) and entry.get("claim_id"),
                "抽查条目须含 claim_id")
        cleaned.append({
            "claim_id": entry["claim_id"],
            "image_ids": list(entry.get("image_ids") or []),
        })
    return _report(store, cleaned, purpose, region, resolution, as_of)


def audit_publication(store, publication_id, as_of,
                      purpose=None, region=None, resolution=None):
    """按冻结快照抽查一部出版物：采用版本是否仍被采用、证据是否漂移、图像是否越权。"""
    pub = store.get("publication", publication_id)
    purpose = purpose or pub["purpose"]
    region = region or pub["region"]
    resolution = resolution or pub["resolution"]
    entries = []
    for item in pub["items"]:
        entries.append({
            "claim_id": item["claim_id"],
            "image_ids": [img["image_id"] for img in item.get("evidence_images", [])],
            "frozen_claim": item["claim"],
            "frozen_evidence_ids": [e["id"] for e in item.get("evidence", [])],
        })
    report = _report(store, entries, purpose, region, resolution, as_of,
                     publication=pub)
    covered = set()
    for entry in report["entries"]:
        for img in entry["images"]:
            covered.add(img["image_id"])
    additional = []
    for iid in sorted(set(pub["image_ids"]) - covered):
        image = store.get("image", iid)
        reasons = image_block_reasons(store, image, purpose, region, resolution, as_of)
        additional.append({
            "image_id": iid,
            "authorized": not reasons,
            "reasons": reasons,
        })
    report["additional_images"] = additional
    report["summary"]["additional_images_blocked"] = sum(
        1 for a in additional if not a["authorized"])
    return report
