"""业务操作：实体维护、观点生命周期、合并、出版冻结、导出与幂等导入。"""

from copy import deepcopy

from .model import (
    ALLOWED_TRANSITIONS,
    CLAIM_KINDS,
    CLAIM_STATUSES,
    COMPLETENESS,
    CONFIDENCE_LEVELS,
    MERGEABLE_ENTITIES,
    RESOLUTION_TIERS,
    SUBJECT_TYPES,
    DomainError,
    require,
    require_choice,
)
from .policy import image_block_reasons, validate_request

# 出版冻结时快照的观点字段（不含状态，状态单独记录）
SNAPSHOT_FIELDS = ("kind", "subject", "statement", "value", "confidence", "supersedes")


# ---------------------------------------------------------------- 机构

def create_institution(store, name, city="", country=""):
    require(name, "机构名称不能为空")
    record = {
        "id": store.new_id("institution"),
        "name": name,
        "city": city,
        "country": country,
        "name_history": [],
    }
    store.add("institution", record)
    store.log("institution.created", institution_id=record["id"], name=name)
    return record


def rename_institution(store, institution_id, new_name):
    """更名不改编号，旧名入历史并记事件，谱系不断。"""
    require(new_name, "新名称不能为空")
    inst = store.get("institution", institution_id)
    old_name = inst["name"]
    require(new_name != old_name, "新名称与现名称相同")
    seq = store.log(
        "institution.renamed",
        institution_id=inst["id"],
        old_name=old_name,
        new_name=new_name,
    )
    inst["name_history"].append({"name": old_name, "changed_seq": seq})
    inst["name"] = new_name
    return inst


# ---------------------------------------------------------------- 作品 / 版本 / 卷册

def create_work(store, title, aliases=None, note=""):
    require(title, "书名不能为空")
    record = {
        "id": store.new_id("work"),
        "title": title,
        "aliases": list(aliases or []),
        "note": note,
    }
    store.add("work", record)
    store.log("work.created", work_id=record["id"], title=title)
    return record


def create_edition(store, work_id, label, note=""):
    work = store.get("work", work_id)
    require(label, "版本标识不能为空")
    record = {
        "id": store.new_id("edition"),
        "work_id": work["id"],
        "label": label,
        "note": note,
    }
    store.add("edition", record)
    store.log("edition.created", edition_id=record["id"], work_id=work["id"], label=label)
    return record


def create_volume(store, institution_id, shelfmark, edition_id=None,
                  completeness="未知", juan_held=None, note=""):
    inst = store.get("institution", institution_id)
    require(shelfmark, "索书号不能为空")
    require_choice(completeness, COMPLETENESS, "完整度")
    edition = store.get("edition", edition_id) if edition_id else None
    record = {
        "id": store.new_id("volume"),
        "institution_id": inst["id"],
        "shelfmark": shelfmark,
        "edition_id": edition["id"] if edition else None,
        "completeness": completeness,
        "juan_held": list(juan_held or []),
        "note": note,
    }
    store.add("volume", record)
    store.log(
        "volume.created",
        volume_id=record["id"],
        institution_id=inst["id"],
        shelfmark=shelfmark,
    )
    return record


def transfer_volume(store, volume_id, institution_id, shelfmark=None):
    """卷册迁移：编号不变，馆藏与索书号更新，事件维系谱系。"""
    vol = store.get("volume", volume_id)
    inst = store.get("institution", institution_id)
    old_inst = vol["institution_id"]
    old_shelf = vol["shelfmark"]
    new_shelf = shelfmark or old_shelf
    require(inst["id"] != old_inst or new_shelf != old_shelf, "迁移目标与现状相同")
    store.log(
        "volume.transferred",
        volume_id=vol["id"],
        from_institution_id=old_inst,
        to_institution_id=inst["id"],
        from_shelfmark=old_shelf,
        to_shelfmark=new_shelf,
    )
    vol["institution_id"] = inst["id"]
    vol["shelfmark"] = new_shelf
    return vol


# ---------------------------------------------------------------- 许可 / 图像

def create_license(store, institution_id, title, purposes, regions,
                   max_resolution, expires_on=None):
    from .model import require_date

    inst = store.get("institution", institution_id)
    require(title, "许可名称不能为空")
    require(isinstance(purposes, list) and purposes, "许可须列明用途")
    require(isinstance(regions, list) and regions, "许可须列明地域")
    require_choice(max_resolution, RESOLUTION_TIERS, "清晰度上限")
    if expires_on is not None:
        require_date(expires_on, "许可到期日")
    record = {
        "id": store.new_id("license"),
        "institution_id": inst["id"],
        "title": title,
        "purposes": sorted(set(purposes)),
        "regions": sorted(set(regions)),
        "max_resolution": max_resolution,
        "expires_on": expires_on,
    }
    store.add("license", record)
    store.log("license.created", license_id=record["id"],
              institution_id=inst["id"], title=title)
    return record


def create_image(store, volume_id, leaf, resolution="high", license_id=None):
    vol = store.get("volume", volume_id)
    require(leaf, "页叶定位不能为空")
    require_choice(resolution, RESOLUTION_TIERS, "图像清晰度")
    lic = store.get("license", license_id) if license_id else None
    record = {
        "id": store.new_id("image"),
        "volume_id": vol["id"],
        "leaf": leaf,
        "resolution": resolution,
        "license_id": lic["id"] if lic else None,
    }
    store.add("image", record)
    store.log("image.created", image_id=record["id"], volume_id=vol["id"], leaf=leaf)
    return record


# ---------------------------------------------------------------- 观点 / 证据 / 评论

def evidence_of(store, claim_id):
    return sorted(
        (e for e in store.bucket("evidence").values() if e["claim_id"] == claim_id),
        key=lambda e: e["id"],
    )


def same_subject(store, subject_a, subject_b):
    """比较两个观点对象，合并墓碑解析到同一存续实体即视为同一对象。"""
    if subject_a["type"] != subject_b["type"]:
        return False
    try:
        return (
            store.get(subject_a["type"], subject_a["id"])["id"]
            == store.get(subject_b["type"], subject_b["id"])["id"]
        )
    except (KeyError, DomainError):
        return subject_a["id"] == subject_b["id"]


def add_claim(store, kind, subject_type, subject_id, statement, confidence, author,
              value=None, rationale="", supersedes=None):
    require_choice(kind, CLAIM_KINDS, "观点类型")
    require_choice(subject_type, SUBJECT_TYPES, "观点对象类型")
    subject = store.get(subject_type, subject_id)
    require(statement, "观点陈述不能为空")
    require_choice(confidence, CONFIDENCE_LEVELS, "置信程度")
    require(author, "须注明提出人")
    if value is not None:
        require(isinstance(value, dict), "观点取值须为对象")
    superseded = store.get("claim", supersedes) if supersedes else None
    record = {
        "id": store.new_id("claim"),
        "kind": kind,
        "subject": {"type": subject_type, "id": subject["id"]},
        "statement": statement,
        "value": value or {},
        "confidence": confidence,
        "status": "draft",
        "author": author,
        "rationale": rationale,
        "supersedes": superseded["id"] if superseded else None,
    }
    store.add("claim", record)
    store.log("claim.created", claim_id=record["id"], kind=kind,
              subject=record["subject"], author=author)
    return record


def add_evidence(store, claim_id, image_id=None, volume_id=None, leaf=None,
                 note="", confidence="medium"):
    """页叶证据：必须可定位——关联图像，或给出卷册与页叶。"""
    claim = store.get("claim", claim_id)
    require_choice(confidence, CONFIDENCE_LEVELS, "证据置信程度")
    image = store.get("image", image_id) if image_id else None
    volume = None
    if image:
        volume = store.get("volume", image["volume_id"])
        leaf = leaf or image["leaf"]
    elif volume_id:
        volume = store.get("volume", volume_id)
    require(volume is not None and leaf, "证据须可定位：关联图像，或给出卷册与页叶")
    record = {
        "id": store.new_id("evidence"),
        "claim_id": claim["id"],
        "image_id": image["id"] if image else None,
        "volume_id": volume["id"],
        "leaf": leaf,
        "note": note,
        "confidence": confidence,
    }
    store.add("evidence", record)
    store.log("evidence.added", evidence_id=record["id"],
              claim_id=claim["id"], leaf=leaf)
    return record


def transition_claim(store, claim_id, status, actor=""):
    """观点状态流转。采用时要求已有页叶证据，并把同对象的竞争性采用观点退回讨论。"""
    require_choice(status, CLAIM_STATUSES, "观点状态")
    claim = store.get("claim", claim_id)
    current = claim["status"]
    require(status != current, f"观点已处于 {current} 状态")
    require(status in ALLOWED_TRANSITIONS[current],
            f"不允许从 {current} 变更为 {status}")
    if status == "adopted":
        require(evidence_of(store, claim_id),
                "采用前须至少引用一条可定位的页叶证据")
    claim["status"] = status
    store.log("claim.status", claim_id=claim["id"],
              from_status=current, to_status=status, actor=actor)
    if status == "adopted":
        for other in store.bucket("claim").values():
            if (
                other["id"] != claim["id"]
                and other["status"] == "adopted"
                and other["kind"] == claim["kind"]
                and same_subject(store, other["subject"], claim["subject"])
            ):
                other["status"] = "discussion"
                store.log("claim.demoted", claim_id=other["id"],
                          by_claim_id=claim["id"])
    return claim


def add_comment(store, claim_id, author, body):
    claim = store.get("claim", claim_id)
    require(author, "须注明评论人")
    require(body, "评论内容不能为空")
    record = {
        "id": store.new_id("comment"),
        "claim_id": claim["id"],
        "author": author,
        "body": body,
    }
    store.add("comment", record)
    store.log("comment.added", comment_id=record["id"],
              claim_id=claim["id"], author=author)
    return record


# ---------------------------------------------------------------- 合并建议

def suggest_merge(store, entity, left_id, right_id, reason, author):
    require_choice(entity, MERGEABLE_ENTITIES, "合并对象类型")
    left = store.get(entity, left_id)
    right = store.get(entity, right_id)
    require(left["id"] != right["id"], "不能合并同一实体")
    require(reason, "须说明合并理由")
    require(author, "须注明建议人")
    record = {
        "id": store.new_id("merge"),
        "entity": entity,
        "left_id": left["id"],
        "right_id": right["id"],
        "reason": reason,
        "author": author,
        "status": "open",
    }
    store.add("merge", record)
    store.log("merge.suggested", merge_id=record["id"], entity=entity,
              left_id=left["id"], right_id=right["id"])
    return record


def resolve_merge(store, merge_id, accept, survivor_id=None, actor=""):
    merge = store.get("merge", merge_id)
    require(merge["status"] == "open", "合并建议已处理")
    require(actor, "须注明处理人")
    if not accept:
        merge["status"] = "rejected"
        store.log("merge.rejected", merge_id=merge["id"], actor=actor)
        return merge
    entity = merge["entity"]
    left = store.get(entity, merge["left_id"])
    right = store.get(entity, merge["right_id"])
    if survivor_id:
        survivor = store.get(entity, survivor_id)
        require(survivor["id"] in (left["id"], right["id"]),
                "存续方须为合并双方之一")
    else:
        survivor = left
    loser = right if survivor["id"] == left["id"] else left
    _absorb(store, entity, survivor, loser)
    loser["merged_into"] = survivor["id"]
    merge["status"] = "accepted"
    merge["survivor_id"] = survivor["id"]
    store.log("merge.accepted", merge_id=merge["id"], entity=entity,
              survivor_id=survivor["id"], absorbed_id=loser["id"], actor=actor)
    return merge


def _absorb(store, entity, survivor, loser):
    """把被合并方的关联实体与异名并入存续方，关系不丢。"""
    if entity == "work":
        for edition in store.bucket("edition").values():
            if edition["work_id"] == loser["id"]:
                edition["work_id"] = survivor["id"]
        titles = [loser["title"], *loser.get("aliases", [])]
        for alias in titles:
            if alias != survivor["title"] and alias not in survivor["aliases"]:
                survivor["aliases"].append(alias)
    elif entity == "edition":
        for volume in store.bucket("volume").values():
            if volume.get("edition_id") == loser["id"]:
                volume["edition_id"] = survivor["id"]
    elif entity == "volume":
        for image in store.bucket("image").values():
            if image["volume_id"] == loser["id"]:
                image["volume_id"] = survivor["id"]
        for juan in loser.get("juan_held", []):
            if juan not in survivor["juan_held"]:
                survivor["juan_held"].append(juan)


# ---------------------------------------------------------------- 出版冻结 / 导出

def freeze_publication(store, title, purpose, region, resolution, claim_ids,
                       image_ids=None, note=""):
    """正式出版：冻结采用观点及其页叶证据快照，之后观点撤回或改选不影响快照。"""
    require(title, "出版物题名不能为空")
    require(purpose, "须注明用途")
    require(region, "须注明地域")
    require_choice(resolution, RESOLUTION_TIERS, "所需清晰度")
    require(isinstance(claim_ids, list) and claim_ids, "出版须至少采用一条观点")
    items = []
    frozen_images = set(image_ids or [])
    for cid in sorted(set(claim_ids)):
        claim = store.get("claim", cid)
        require(claim["status"] == "adopted",
                f"观点 {cid} 未处于采用状态，不能随出版冻结")
        evidence = evidence_of(store, cid)
        images = []
        for ev in evidence:
            if ev.get("image_id"):
                img = store.get("image", ev["image_id"])
                vol = store.get("volume", img["volume_id"])
                images.append({
                    "image_id": img["id"],
                    "leaf": img["leaf"],
                    "volume_id": vol["id"],
                    "institution_id": vol["institution_id"],
                })
                frozen_images.add(img["id"])
        items.append({
            "claim_id": claim["id"],
            "claim": {k: deepcopy(claim[k]) for k in SNAPSHOT_FIELDS},
            "status_at_freeze": claim["status"],
            "evidence": deepcopy(evidence),
            "evidence_images": images,
        })
    for iid in sorted(frozen_images):
        store.get("image", iid)
    record = {
        "id": store.new_id("publication"),
        "title": title,
        "purpose": purpose,
        "region": region,
        "resolution": resolution,
        "note": note,
        "items": items,
        "image_ids": sorted(frozen_images),
    }
    store.add("publication", record)
    store.log("publication.frozen", publication_id=record["id"], title=title,
              claims=[i["claim_id"] for i in items])
    return record


def build_export(store, image_ids, purpose, region, resolution, as_of):
    """导出展览或书稿材料：逐项评估许可，越权图像阻断并给出理由。"""
    validate_request(purpose, region, resolution, as_of)
    require(isinstance(image_ids, list) and image_ids, "须给出图像清单")
    allowed = []
    blocked = []
    for iid in sorted(set(image_ids)):
        image = store.get("image", iid)
        reasons = image_block_reasons(store, image, purpose, region, resolution, as_of)
        if reasons:
            blocked.append({"image_id": iid, "reasons": reasons})
        else:
            allowed.append(iid)
    return {
        "request": {
            "purpose": purpose,
            "region": region,
            "resolution": resolution,
            "as_of": as_of,
        },
        "allowed": allowed,
        "blocked": blocked,
    }


# ---------------------------------------------------------------- 幂等导入

def _find_one(bucket, **criteria):
    for record in bucket.values():
        if all(record.get(k) == v for k, v in criteria.items()):
            return record
    return None


def ingest(store, payload):
    """按自然键去重导入：重复导入同一批数据不会产生第二份卷册。

    自然键：机构=名称，作品=书名，版本=作品+标识，许可=机构+名称，
    卷册=机构+索书号，图像=卷册+页叶，观点=对象+类型+陈述，出版物=题名。
    """
    require(isinstance(payload, dict), "导入内容须为对象")
    report = {"created": {}, "reused": {}}

    def mark(kind, record, created):
        report["created" if created else "reused"].setdefault(kind, []).append(record["id"])

    institutions = {}
    for item in payload.get("institutions", []):
        existing = _find_one(store.bucket("institution"), name=item["name"])
        created = existing is None
        if created:
            existing = create_institution(store, item["name"],
                                          item.get("city", ""), item.get("country", ""))
        mark("institution", existing, created)
        institutions[item["name"]] = existing

    works = {}
    for item in payload.get("works", []):
        existing = _find_one(store.bucket("work"), title=item["title"])
        created = existing is None
        if created:
            existing = create_work(store, item["title"],
                                   item.get("aliases"), item.get("note", ""))
        else:
            for alias in item.get("aliases", []):
                if alias not in existing["aliases"]:
                    existing["aliases"].append(alias)
        mark("work", existing, created)
        works[item["title"]] = existing

    editions = {}
    for item in payload.get("editions", []):
        work = works.get(item["work"]) or _find_one(store.bucket("work"), title=item["work"])
        require(work is not None, f"版本所属作品不存在: {item.get('work')}")
        existing = _find_one(store.bucket("edition"),
                             work_id=work["id"], label=item["label"])
        created = existing is None
        if created:
            existing = create_edition(store, work["id"], item["label"],
                                      item.get("note", ""))
        mark("edition", existing, created)
        editions[(item["work"], item["label"])] = existing

    licenses = {}
    for item in payload.get("licenses", []):
        inst = institutions.get(item["institution"]) or _find_one(
            store.bucket("institution"), name=item["institution"])
        require(inst is not None, f"许可所属机构不存在: {item.get('institution')}")
        existing = _find_one(store.bucket("license"),
                             institution_id=inst["id"], title=item["title"])
        created = existing is None
        if created:
            existing = create_license(store, inst["id"], item["title"],
                                      item["purposes"], item["regions"],
                                      item["max_resolution"], item.get("expires_on"))
        mark("license", existing, created)
        licenses[(item["institution"], item["title"])] = existing

    volumes = {}
    for item in payload.get("volumes", []):
        inst = institutions.get(item["institution"]) or _find_one(
            store.bucket("institution"), name=item["institution"])
        require(inst is not None, f"卷册所属机构不存在: {item.get('institution')}")
        edition = None
        if item.get("edition"):
            key = (item["edition"]["work"], item["edition"]["label"])
            edition = editions.get(key)
            if edition is None:
                work = _find_one(store.bucket("work"), title=key[0])
                edition = _find_one(store.bucket("edition"),
                                    work_id=work["id"], label=key[1]) if work else None
            require(edition is not None, f"卷册所属版本不存在: {key}")
        existing = _find_one(store.bucket("volume"),
                             institution_id=inst["id"], shelfmark=item["shelfmark"])
        created = existing is None
        if created:
            existing = create_volume(
                store, inst["id"], item["shelfmark"],
                edition_id=edition["id"] if edition else None,
                completeness=item.get("completeness", "未知"),
                juan_held=item.get("juan_held"),
                note=item.get("note", ""),
            )
        mark("volume", existing, created)
        volumes[(item["institution"], item["shelfmark"])] = existing

    images = {}
    for item in payload.get("images", []):
        volume = _lookup_volume(store, volumes, item["institution"], item["shelfmark"])
        existing = _find_one(store.bucket("image"),
                             volume_id=volume["id"], leaf=item["leaf"])
        created = existing is None
        if created:
            lic = None
            if item.get("license"):
                lic = licenses.get((item["institution"], item["license"])) or _find_one(
                    store.bucket("license"),
                    institution_id=volume["institution_id"], title=item["license"])
                require(lic is not None, f"图像许可不存在: {item['license']}")
            existing = create_image(store, volume["id"], item["leaf"],
                                    item.get("resolution", "high"),
                                    license_id=lic["id"] if lic else None)
        mark("image", existing, created)
        images[(item["institution"], item["shelfmark"], item["leaf"])] = existing

    claim_refs = {}
    for item in payload.get("claims", []):
        subject = _resolve_subject(store, item["subject"], works, editions, volumes)
        existing = None
        for cand in store.bucket("claim").values():
            if (
                cand["kind"] == item["kind"]
                and cand["statement"] == item["statement"]
                and cand["subject"] == {"type": item["subject"]["type"], "id": subject["id"]}
            ):
                existing = cand
                break
        created = existing is None
        if created:
            supersedes = None
            if item.get("supersedes"):
                require(item["supersedes"] in claim_refs,
                        f"修订引用了未知观点: {item['supersedes']}")
                supersedes = claim_refs[item["supersedes"]]["id"]
            existing = add_claim(
                store, item["kind"], item["subject"]["type"], subject["id"],
                item["statement"], item["confidence"], item["author"],
                value=item.get("value"), rationale=item.get("rationale", ""),
                supersedes=supersedes,
            )
            for ev in item.get("evidence", []):
                _ingest_evidence(store, existing, ev, images, volumes)
            for cmt in item.get("comments", []):
                add_comment(store, existing["id"], cmt["author"], cmt["body"])
            target = item.get("status", "draft")
            if target in ("discussion", "adopted"):
                transition_claim(store, existing["id"], "discussion",
                                 actor=item["author"])
            if target == "adopted":
                transition_claim(store, existing["id"], "adopted",
                                 actor=item["author"])
        mark("claim", existing, created)
        if item.get("ref"):
            claim_refs[item["ref"]] = existing

    for item in payload.get("publications", []):
        existing = _find_one(store.bucket("publication"), title=item["title"])
        if existing is not None:
            mark("publication", existing, False)
            continue
        claim_ids = []
        for ref in item.get("claims", []):
            require(ref in claim_refs, f"出版引用了未知观点: {ref}")
            claim_ids.append(claim_refs[ref]["id"])
        image_ids = []
        for key in item.get("images", []):
            image = _lookup_image(store, images,
                                  key["institution"], key["shelfmark"], key["leaf"])
            image_ids.append(image["id"])
        pub = freeze_publication(store, item["title"], item["purpose"],
                                 item["region"], item["resolution"], claim_ids,
                                 image_ids=image_ids, note=item.get("note", ""))
        mark("publication", pub, True)

    return report


def _lookup_volume(store, volumes, institution_name, shelfmark):
    key = (institution_name, shelfmark)
    volume = volumes.get(key)
    if volume is None:
        inst = _find_one(store.bucket("institution"), name=institution_name)
        volume = _find_one(store.bucket("volume"),
                           institution_id=inst["id"], shelfmark=shelfmark) if inst else None
    require(volume is not None, f"卷册不存在: {institution_name} {shelfmark}")
    return volume


def _lookup_image(store, images, institution_name, shelfmark, leaf):
    key = (institution_name, shelfmark, leaf)
    image = images.get(key)
    if image is None:
        volume = _lookup_volume(store, {}, institution_name, shelfmark)
        image = _find_one(store.bucket("image"), volume_id=volume["id"], leaf=leaf)
    require(image is not None, f"图像不存在: {institution_name} {shelfmark} {leaf}")
    return image


def _resolve_subject(store, subject, works, editions, volumes):
    stype = subject.get("type")
    if stype == "work":
        record = works.get(subject["title"]) or _find_one(
            store.bucket("work"), title=subject["title"])
    elif stype == "edition":
        record = editions.get((subject["work"], subject["label"]))
        if record is None:
            work = _find_one(store.bucket("work"), title=subject["work"])
            record = _find_one(store.bucket("edition"),
                               work_id=work["id"], label=subject["label"]) if work else None
    elif stype == "volume":
        record = volumes.get((subject["institution"], subject["shelfmark"]))
        if record is None:
            inst = _find_one(store.bucket("institution"), name=subject["institution"])
            record = _find_one(store.bucket("volume"),
                               institution_id=inst["id"],
                               shelfmark=subject["shelfmark"]) if inst else None
    else:
        raise DomainError(f"未知观点对象类型: {stype}")
    require(record is not None, f"观点对象不存在: {subject}")
    return record


def _ingest_evidence(store, claim, ev, images, volumes):
    image_id = None
    volume_id = None
    leaf = ev.get("leaf")
    if ev.get("image"):
        image = _lookup_image(store, images, ev["image"]["institution"],
                              ev["image"]["shelfmark"], ev["image"]["leaf"])
        image_id = image["id"]
    elif ev.get("volume"):
        volume = _lookup_volume(store, volumes,
                                ev["volume"]["institution"], ev["volume"]["shelfmark"])
        volume_id = volume["id"]
    add_evidence(store, claim["id"], image_id=image_id, volume_id=volume_id,
                 leaf=leaf, note=ev.get("note", ""),
                 confidence=ev.get("confidence", "medium"))
