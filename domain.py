"""海外古籍研究谱系库的领域模型与业务规则。

作品、版本、实体卷册、馆藏机构与数字图像分别建模；断代、作者、
套印技法与流传路径等结论一律以"观点"表达，必须引用可定位的页叶
证据并标注置信程度。竞争性观点并存，正式出版冻结采用观点的证据
快照；撤回观点只改状态、不删除历史引用。机构更名与卷册迁移以事件
记录，谱系不因属性变化而割断。许可校验、导出阻断与反向抽查均为
确定性实现：同样的输入必然产生同样的结果。
"""

from datetime import date

CLAIM_KINDS = ("dating", "authorship", "technique", "transmission")
CONFIDENCE_LEVELS = ("高", "中", "低")
CLAIM_STATUSES = ("draft", "discussion", "adopted", "withdrawn")
CLAIM_TRANSITIONS = {
    "draft": {"discussion", "withdrawn"},
    "discussion": {"adopted", "withdrawn"},
    "adopted": {"withdrawn"},
    "withdrawn": set(),
}
USAGES = ("research", "education", "exhibition", "publication")
RESOLUTION_RANK = {"low": 1, "medium": 2, "high": 3}
ENTITY_PREFIXES = {
    "work": "W",
    "edition": "E",
    "volume": "V",
    "institution": "N",
    "image": "G",
    "license": "L",
    "claim": "C",
    "comment": "K",
    "merge": "M",
    "publication": "P",
}
COLLECTIONS = {
    "works": "work",
    "editions": "edition",
    "volumes": "volume",
    "institutions": "institution",
    "images": "image",
    "licenses": "license",
    "claims": "claim",
    "comments": "comment",
    "merges": "merge",
    "publications": "publication",
}
MERGEABLE_KINDS = ("work", "edition", "volume", "institution")
SUBJECT_KINDS = ("work", "edition", "volume")


class DomainError(Exception):
    """业务规则错误，status 供 HTTP 层映射。"""

    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def _norm(text):
    """规范化用于自然键的字符串。"""
    return " ".join(str(text).split())


def _require_text(value, field):
    if not isinstance(value, str) or not value.strip():
        raise DomainError(f"字段 {field} 不能为空")
    return value.strip()


def _check_date(value, field):
    try:
        date.fromisoformat(value)
    except (TypeError, ValueError):
        raise DomainError(f"字段 {field} 必须是 YYYY-MM-DD 格式的日期")
    return value


class Store:
    """谱系库的唯一数据源，只保存可 JSON 序列化的普通字典。"""

    def __init__(self):
        self.entities = {kind: {} for kind in ENTITY_PREFIXES}
        self.counters = {kind: 0 for kind in ENTITY_PREFIXES}
        self.aliases = {kind: {} for kind in MERGEABLE_KINDS}
        self.seq = 0
        # 自然键索引：重复导入据此去重，不产生第二份记录。
        self._work_titles = {}        # 规范化书名/异名 -> work_id
        self._institution_names = {}  # 馆名（含历史名）-> institution_id
        self._edition_keys = {}       # (work_id, 版本著录) -> edition_id
        self._volume_keys = {}        # (institution_id, 索书号) -> volume_id
        self._image_keys = {}         # (volume_id, 页叶) -> image_id
        self._license_keys = {}       # (institution_id, 许可名) -> license_id

    # ---------- 基础工具 ----------

    def _tick(self):
        self.seq += 1
        return self.seq

    def _new_id(self, kind):
        self.counters[kind] += 1
        return f"{ENTITY_PREFIXES[kind]}{self.counters[kind]:04d}"

    def resolve(self, kind, entity_id):
        """沿合并别名链解析到当前实体；谱系记录仍保留在原 id 上。"""
        seen = set()
        while entity_id in self.aliases.get(kind, {}):
            if entity_id in seen:
                raise DomainError("合并链存在环路", 500)
            seen.add(entity_id)
            entity_id = self.aliases[kind][entity_id]
        return entity_id

    def get(self, kind, entity_id):
        rid = self.resolve(kind, entity_id)
        entity = self.entities[kind].get(rid)
        if entity is None:
            raise DomainError(f"未找到{kind}：{entity_id}", 404)
        return entity

    def _raw(self, kind, entity_id):
        entity = self.entities[kind].get(entity_id)
        if entity is None:
            raise DomainError(f"未找到{kind}：{entity_id}", 404)
        return entity

    def merged_into(self, kind, entity_id):
        rid = self.resolve(kind, entity_id)
        return rid if rid != entity_id else None

    # ---------- 馆藏机构：更名不断谱系 ----------

    def add_institution(self, name, aliases=()):
        name = _require_text(name, "name")
        candidates = [_norm(name)] + [_norm(a) for a in aliases]
        for key in candidates:
            if key in self._institution_names:
                return self._institution_names[key], False
        iid = self._new_id("institution")
        self.entities["institution"][iid] = {
            "id": iid,
            "names": [name],
            "aliases": list(aliases),
            "name_events": [{"name": name, "seq": self._tick(), "note": "初始著录"}],
        }
        for key in candidates:
            self._institution_names[key] = iid
        return iid, True

    def rename_institution(self, institution_id, name, note="机构更名"):
        inst = self.get("institution", institution_id)
        name = _require_text(name, "name")
        if name == inst["names"][-1]:
            raise DomainError("新馆名与现名相同", 409)
        inst["names"].append(name)
        inst["name_events"].append({"name": name, "seq": self._tick(), "note": note})
        self._institution_names[_norm(name)] = inst["id"]
        return inst

    # ---------- 作品与版本 ----------

    def add_work(self, title, aliases=()):
        title = _require_text(title, "title")
        candidates = [_norm(title)] + [_norm(a) for a in aliases]
        for key in candidates:
            if key in self._work_titles:
                return self._work_titles[key], False
        wid = self._new_id("work")
        self.entities["work"][wid] = {
            "id": wid,
            "title": title,
            "aliases": list(aliases),
        }
        for key in candidates:
            self._work_titles[key] = wid
        return wid, True

    def add_edition(self, work_id, statement, description=""):
        work = self.get("work", work_id)
        statement = _require_text(statement, "statement")
        key = (work["id"], _norm(statement))
        if key in self._edition_keys:
            return self._edition_keys[key], False
        eid = self._new_id("edition")
        self.entities["edition"][eid] = {
            "id": eid,
            "work_id": work["id"],
            "statement": statement,
            "description": description,
        }
        self._edition_keys[key] = eid
        return eid, True

    # ---------- 实体卷册：迁移不断谱系，重复导入去重 ----------

    def add_volume(self, edition_id, institution_id, shelfmark, fragment=False, label=""):
        edition = self.get("edition", edition_id)
        inst = self.get("institution", institution_id)
        shelfmark = _require_text(shelfmark, "shelfmark")
        key = (inst["id"], _norm(shelfmark))
        if key in self._volume_keys:
            return self._volume_keys[key], False
        vid = self._new_id("volume")
        self.entities["volume"][vid] = {
            "id": vid,
            "edition_id": edition["id"],
            "shelfmark": shelfmark,
            "fragment": bool(fragment),
            "label": label,
            "holdings": [
                {"institution_id": inst["id"], "seq": self._tick(), "note": "初始入藏"}
            ],
        }
        self._volume_keys[key] = vid
        return vid, True

    def migrate_volume(self, volume_id, institution_id, note="卷册迁移"):
        volume = self.get("volume", volume_id)
        inst = self.get("institution", institution_id)
        if volume["holdings"][-1]["institution_id"] == inst["id"]:
            raise DomainError("卷册已登记在该机构名下", 409)
        volume["holdings"].append(
            {"institution_id": inst["id"], "seq": self._tick(), "note": note}
        )
        # 迁移后的（新馆, 索书号）同样登记为自然键，重复导入仍命中同一卷。
        self._volume_keys[(inst["id"], _norm(volume["shelfmark"]))] = volume["id"]
        return volume

    def volume_lineage(self, volume_id):
        volume = self.get("volume", volume_id)
        holdings = [
            {
                "institution_id": h["institution_id"],
                "institution_name": self.get("institution", h["institution_id"])["names"][-1],
                "seq": h["seq"],
                "note": h["note"],
            }
            for h in sorted(volume["holdings"], key=lambda h: h["seq"])
        ]
        result = {"volume_id": volume["id"], "shelfmark": volume["shelfmark"], "holdings": holdings}
        merged = self.merged_into("volume", volume["id"])
        if merged:
            result["merged_into"] = merged
        return result

    # ---------- 数字图像与许可 ----------

    def add_image(self, volume_id, leaf, license_id=None, note=""):
        volume = self.get("volume", volume_id)
        leaf = _require_text(leaf, "leaf")
        license_rid = None
        if license_id is not None:
            license_rid = self.get("license", license_id)["id"]
        key = (volume["id"], _norm(leaf))
        if key in self._image_keys:
            return self._image_keys[key], False
        gid = self._new_id("image")
        self.entities["image"][gid] = {
            "id": gid,
            "volume_id": volume["id"],
            "leaf": leaf,
            "license_id": license_rid,
            "note": note,
        }
        self._image_keys[key] = gid
        return gid, True

    def add_license(self, institution_id, name, usages, regions, max_resolution, expires_on=None):
        inst = self.get("institution", institution_id)
        name = _require_text(name, "name")
        if not usages or any(u not in USAGES for u in usages):
            raise DomainError(f"usages 必须取自 {list(USAGES)}")
        if not regions or any(not _norm(r) for r in regions):
            raise DomainError("regions 不能为空")
        if max_resolution not in RESOLUTION_RANK:
            raise DomainError(f"max_resolution 必须取自 {list(RESOLUTION_RANK)}")
        if expires_on is not None:
            _check_date(expires_on, "expires_on")
        key = (inst["id"], _norm(name))
        if key in self._license_keys:
            return self._license_keys[key], False
        lid = self._new_id("license")
        self.entities["license"][lid] = {
            "id": lid,
            "institution_id": inst["id"],
            "name": name,
            "usages": sorted(set(usages)),
            "regions": sorted({_norm(r) for r in regions}),
            "max_resolution": max_resolution,
            "expires_on": expires_on,
        }
        self._license_keys[key] = lid
        return lid, True

    # ---------- 观点：证据、置信度、状态机、修订 ----------

    def _normalize_evidence(self, evidence):
        if not evidence:
            raise DomainError("观点必须引用至少一条页叶证据")
        normalized = []
        for item in evidence:
            if not isinstance(item, dict):
                raise DomainError("证据条目必须是对象")
            if "image_id" in item:
                image = self.get("image", item["image_id"])
                normalized.append({"image_id": image["id"]})
            elif "volume_id" in item and "leaf" in item:
                volume = self.get("volume", item["volume_id"])
                leaf = _require_text(item["leaf"], "leaf")
                normalized.append({"volume_id": volume["id"], "leaf": leaf})
            else:
                raise DomainError("证据必须引用数字图像（image_id）或卷册页叶（volume_id + leaf）")
        return normalized

    def add_claim(self, kind, subject, statement, confidence, evidence, author, revises=None):
        if kind not in CLAIM_KINDS:
            raise DomainError(f"观点类型必须取自 {list(CLAIM_KINDS)}")
        if confidence not in CONFIDENCE_LEVELS:
            raise DomainError(f"置信程度必须取自 {list(CONFIDENCE_LEVELS)}")
        if not isinstance(subject, dict) or subject.get("type") not in SUBJECT_KINDS:
            raise DomainError(f"观点对象类型必须取自 {list(SUBJECT_KINDS)}")
        target = self.get(subject["type"], _require_text(subject.get("id", ""), "subject.id"))
        statement = _require_text(statement, "statement")
        author = _require_text(author, "author")
        normalized = self._normalize_evidence(evidence)
        version = 1
        revises_id = None
        if revises is not None:
            base = self.get("claim", revises)
            if base["kind"] != kind or base["subject"] != {"type": subject["type"], "id": target["id"]}:
                raise DomainError("修订必须沿用原观点的类型与对象", 409)
            if base.get("superseded_by"):
                raise DomainError("该观点已有更新的修订，应基于最新版本修订", 409)
            revises_id = base["id"]
            version = base["version"] + 1
        cid = self._new_id("claim")
        self.entities["claim"][cid] = {
            "id": cid,
            "kind": kind,
            "subject": {"type": subject["type"], "id": target["id"]},
            "statement": statement,
            "confidence": confidence,
            "evidence": normalized,
            "author": author,
            "status": "draft",
            "version": version,
            "revises": revises_id,
            "superseded_by": None,
            "seq": self._tick(),
        }
        if revises_id is not None:
            self.entities["claim"][revises_id]["superseded_by"] = cid
        return cid

    def transition_claim(self, claim_id, to):
        claim = self.get("claim", claim_id)
        if to not in CLAIM_STATUSES:
            raise DomainError(f"状态必须取自 {list(CLAIM_STATUSES)}")
        if to not in CLAIM_TRANSITIONS[claim["status"]]:
            raise DomainError(f"不允许从 {claim['status']} 变为 {to}", 409)
        claim["status"] = to
        claim["seq"] = self._tick()
        return claim

    def add_comment(self, claim_id, author, body):
        claim = self.get("claim", claim_id)
        author = _require_text(author, "author")
        body = _require_text(body, "body")
        kid = self._new_id("comment")
        self.entities["comment"][kid] = {
            "id": kid,
            "claim_id": claim["id"],
            "author": author,
            "body": body,
            "seq": self._tick(),
        }
        return kid

    # ---------- 合并建议：接受后保留谱系，不删除原记录 ----------

    def add_merge(self, kind, source_id, target_id, reason):
        if kind not in MERGEABLE_KINDS:
            raise DomainError(f"合并类型必须取自 {list(MERGEABLE_KINDS)}")
        self._raw(kind, source_id)
        self._raw(kind, target_id)
        if source_id == target_id:
            raise DomainError("不能与自身合并")
        if self.merged_into(kind, source_id):
            raise DomainError("来源记录已并入其他实体", 409)
        mid = self._new_id("merge")
        self.entities["merge"][mid] = {
            "id": mid,
            "kind": kind,
            "source_id": source_id,
            "target_id": target_id,
            "reason": _require_text(reason, "reason"),
            "status": "open",
            "seq": self._tick(),
        }
        return mid

    def resolve_merge(self, merge_id, accept):
        merge = self.get("merge", merge_id)
        if merge["status"] != "open":
            raise DomainError("合并建议已处理", 409)
        merge["status"] = "accepted" if accept else "rejected"
        merge["seq"] = self._tick()
        if accept:
            self.aliases[merge["kind"]][merge["source_id"]] = merge["target_id"]
        return merge

    # ---------- 正式出版：冻结采用观点及其证据快照 ----------

    def _subject_label(self, subject):
        kind, rid = subject["type"], subject["id"]
        if kind == "work":
            return self.get("work", rid)["title"]
        if kind == "edition":
            edition = self.get("edition", rid)
            return f"{self.get('work', edition['work_id'])['title']}｜{edition['statement']}"
        volume = self.get("volume", rid)
        inst = self.get("institution", volume["holdings"][-1]["institution_id"])
        return f"{volume['shelfmark']}｜{inst['names'][-1]}"

    def _freeze_evidence(self, evidence):
        frozen = []
        for item in evidence:
            if "image_id" in item:
                image = self.get("image", item["image_id"])
                volume = self.get("volume", image["volume_id"])
                inst = self.get("institution", volume["holdings"][-1]["institution_id"])
                frozen.append({
                    "image_id": image["id"],
                    "volume_id": volume["id"],
                    "leaf": image["leaf"],
                    "institution_id": inst["id"],
                    "institution_name": inst["names"][-1],
                    "license_id": image["license_id"],
                })
            else:
                volume = self.get("volume", item["volume_id"])
                inst = self.get("institution", volume["holdings"][-1]["institution_id"])
                frozen.append({
                    "image_id": None,
                    "volume_id": volume["id"],
                    "leaf": item["leaf"],
                    "institution_id": inst["id"],
                    "institution_name": inst["names"][-1],
                    "license_id": None,
                })
        return frozen

    def create_publication(self, title, claim_ids, note=""):
        title = _require_text(title, "title")
        if not claim_ids:
            raise DomainError("出版清单不能为空")
        items = []
        for claim_id in claim_ids:
            claim = self.get("claim", claim_id)
            if claim["status"] != "adopted":
                raise DomainError(f"观点 {claim['id']} 未处于采用状态，不能冻结出版", 409)
            items.append({
                "claim_id": claim["id"],
                "version": claim["version"],
                "kind": claim["kind"],
                "statement": claim["statement"],
                "confidence": claim["confidence"],
                "author": claim["author"],
                "status_at_freeze": claim["status"],
                "subject": {**claim["subject"], "label": self._subject_label(claim["subject"])},
                "evidence": self._freeze_evidence(claim["evidence"]),
            })
        pid = self._new_id("publication")
        self.entities["publication"][pid] = {
            "id": pid,
            "title": title,
            "note": note,
            "items": items,
            "seq": self._tick(),
        }
        return pid

    # ---------- 许可判定与导出阻断 ----------

    @staticmethod
    def _validate_export_params(usage, region, resolution, as_of):
        if usage not in USAGES:
            raise DomainError(f"usage 必须取自 {list(USAGES)}")
        if resolution not in RESOLUTION_RANK:
            raise DomainError(f"resolution 必须取自 {list(RESOLUTION_RANK)}")
        _require_text(region, "region")
        _check_date(as_of, "as_of")

    @staticmethod
    def license_verdict(license_, usage, region, resolution, as_of):
        """返回阻断原因列表；空列表表示放行。原因顺序固定以保证确定性。"""
        if license_ is None:
            return ["无许可记录"]
        reasons = []
        if usage not in license_["usages"]:
            reasons.append("用途超出许可范围")
        if "WORLD" not in license_["regions"] and region not in license_["regions"]:
            reasons.append("地域超出许可范围")
        if RESOLUTION_RANK[resolution] > RESOLUTION_RANK[license_["max_resolution"]]:
            reasons.append("清晰度超出许可范围")
        if license_["expires_on"] is not None and as_of > license_["expires_on"]:
            reasons.append("许可已过期")
        return reasons

    def export_check(self, image_ids, usage, region, resolution, as_of):
        """逐项检查导出清单，越权图像被阻断并给出原因。"""
        self._validate_export_params(usage, region, resolution, as_of)
        if not image_ids:
            raise DomainError("image_ids 不能为空")
        items = []
        for image_id in image_ids:
            image = self.entities["image"].get(self.resolve("image", image_id))
            if image is None:
                items.append({"image_id": image_id, "allowed": False, "reasons": ["图像不存在"]})
                continue
            license_ = (
                self.entities["license"].get(image["license_id"])
                if image["license_id"]
                else None
            )
            reasons = self.license_verdict(license_, usage, region, resolution, as_of)
            volume = self.get("volume", image["volume_id"])
            items.append({
                "image_id": image["id"],
                "volume_id": volume["id"],
                "leaf": image["leaf"],
                "institution_id": volume["holdings"][-1]["institution_id"],
                "license_id": image["license_id"],
                "allowed": not reasons,
                "reasons": reasons,
            })
        blocked = sum(1 for item in items if not item["allowed"])
        return {
            "usage": usage,
            "region": region,
            "resolution": resolution,
            "as_of": as_of,
            "items": items,
            "summary": {"total": len(items), "allowed": len(items) - blocked, "blocked": blocked},
        }

    # ---------- 反向抽查：跨机构出版清单审计 ----------

    def _chain_head(self, claim_id):
        claim = self.get("claim", claim_id)
        while claim.get("superseded_by"):
            claim = self.entities["claim"][claim["superseded_by"]]
        return claim

    def audit_publication(self, publication_id, usage, region, resolution, as_of):
        """对出版清单逐条核对采用版本、页叶证据与当前授权状态。"""
        self._validate_export_params(usage, region, resolution, as_of)
        pub = self.get("publication", publication_id)
        items = []
        by_institution = {}
        blocked_total = 0
        evidence_total = 0
        for snap in pub["items"]:
            live = self.get("claim", snap["claim_id"])
            head = self._chain_head(snap["claim_id"])
            evidence = []
            for ev in snap["evidence"]:
                if ev["image_id"] is not None:
                    image = self.get("image", ev["image_id"])
                    license_ = (
                        self.entities["license"].get(image["license_id"])
                        if image["license_id"]
                        else None
                    )
                    reasons = self.license_verdict(license_, usage, region, resolution, as_of)
                    authorization = {
                        "status": "blocked" if reasons else "ok",
                        "reasons": reasons,
                    }
                else:
                    reasons = []
                    authorization = {"status": "no_image", "reasons": reasons}
                current_inst = self.get("institution", ev["institution_id"])
                evidence.append({
                    **ev,
                    "current_institution_name": current_inst["names"][-1],
                    "authorization": authorization,
                })
                evidence_total += 1
                if authorization["status"] == "blocked":
                    blocked_total += 1
                bucket = by_institution.setdefault(ev["institution_id"], {
                    "institution_id": ev["institution_id"],
                    "institution_name": current_inst["names"][-1],
                    "evidence": 0,
                    "blocked": 0,
                })
                bucket["evidence"] += 1
                if authorization["status"] == "blocked":
                    bucket["blocked"] += 1
            items.append({
                "claim_id": snap["claim_id"],
                "adopted_version": snap["version"],
                "latest_version": head["version"],
                "is_latest": head["id"] == snap["claim_id"],
                "kind": snap["kind"],
                "statement": snap["statement"],
                "confidence": snap["confidence"],
                "status_at_freeze": snap["status_at_freeze"],
                "current_status": live["status"],
                "subject": snap["subject"],
                "evidence": evidence,
            })
        return {
            "publication_id": pub["id"],
            "title": pub["title"],
            "usage": usage,
            "region": region,
            "resolution": resolution,
            "as_of": as_of,
            "items": items,
            "by_institution": [by_institution[k] for k in sorted(by_institution)],
            "summary": {
                "claims": len(items),
                "evidence": evidence_total,
                "blocked": blocked_total,
            },
        }

    # ---------- 批量导入：按自然键去重，重复导入幂等 ----------

    def import_bundle(self, bundle):
        """按书目自然键导入；返回值与再次导入同一数据的结果完全一致。"""
        report = {key: [] for key in (
            "institutions", "works", "editions", "volumes", "licenses", "images"
        )}
        for entry in bundle.get("institutions", []):
            iid, created = self.add_institution(entry["name"], entry.get("aliases", []))
            report["institutions"].append({"name": entry["name"], "id": iid, "created": created})
        for entry in bundle.get("works", []):
            wid, created = self.add_work(entry["title"], entry.get("aliases", []))
            report["works"].append({"title": entry["title"], "id": wid, "created": created})
        for entry in bundle.get("editions", []):
            wid = self._work_titles.get(_norm(_require_text(entry.get("work", ""), "work")))
            if wid is None:
                raise DomainError(f"版本所属作品未导入：{entry.get('work')}", 404)
            eid, created = self.add_edition(
                wid, entry["statement"], entry.get("description", "")
            )
            report["editions"].append({
                "work": entry["work"], "statement": entry["statement"],
                "id": eid, "created": created,
            })
        for entry in bundle.get("volumes", []):
            wid = self._work_titles.get(_norm(_require_text(entry.get("work", ""), "work")))
            iid = self._institution_names.get(
                _norm(_require_text(entry.get("institution", ""), "institution"))
            )
            if wid is None:
                raise DomainError(f"卷册所属作品未导入：{entry.get('work')}", 404)
            if iid is None:
                raise DomainError(f"卷册所属机构未导入：{entry.get('institution')}", 404)
            eid = self._edition_keys.get((wid, _norm(_require_text(entry.get("edition", ""), "edition"))))
            if eid is None:
                raise DomainError(f"卷册所属版本未导入：{entry.get('edition')}", 404)
            vid, created = self.add_volume(
                eid, iid, entry["shelfmark"], entry.get("fragment", False), entry.get("label", "")
            )
            report["volumes"].append({
                "institution": entry["institution"], "shelfmark": entry["shelfmark"],
                "id": vid, "created": created,
            })
        for entry in bundle.get("licenses", []):
            iid = self._institution_names.get(
                _norm(_require_text(entry.get("institution", ""), "institution"))
            )
            if iid is None:
                raise DomainError(f"许可所属机构未导入：{entry.get('institution')}", 404)
            lid, created = self.add_license(
                iid, entry["name"], entry.get("usages", []), entry.get("regions", []),
                entry.get("max_resolution", ""), entry.get("expires_on"),
            )
            report["licenses"].append({
                "institution": entry["institution"], "name": entry["name"],
                "id": lid, "created": created,
            })
        for entry in bundle.get("images", []):
            iid = self._institution_names.get(
                _norm(_require_text(entry.get("institution", ""), "institution"))
            )
            if iid is None:
                raise DomainError(f"图像所属机构未导入：{entry.get('institution')}", 404)
            vid = self._volume_keys.get(
                (iid, _norm(_require_text(entry.get("shelfmark", ""), "shelfmark")))
            )
            if vid is None:
                raise DomainError(f"图像所属卷册未导入：{entry.get('shelfmark')}", 404)
            lid = None
            if entry.get("license"):
                lid = self._license_keys.get((iid, _norm(entry["license"])))
                if lid is None:
                    raise DomainError(f"图像许可未导入：{entry['license']}", 404)
            gid, created = self.add_image(vid, entry["leaf"], lid, entry.get("note", ""))
            report["images"].append({
                "shelfmark": entry["shelfmark"], "leaf": entry["leaf"],
                "id": gid, "created": created,
            })
        return report

    # ---------- 视图与序列化 ----------

    def view(self, kind, entity_id):
        """面向查询的实体视图：补充派生字段与合并指向。"""
        raw = self._raw(kind, entity_id)
        resolved = self.resolve(kind, entity_id)
        entity = dict(self.entities[kind][resolved])
        if kind == "institution":
            entity["name"] = entity["names"][-1]
        elif kind == "volume":
            holder = self.get("institution", entity["holdings"][-1]["institution_id"])
            entity["current_institution_id"] = holder["id"]
            entity["current_institution_name"] = holder["names"][-1]
        if resolved != entity_id:
            entity["merged_into"] = resolved
        return entity

    def list(self, kind):
        return [self.view(kind, eid) for eid in sorted(self.entities[kind])]

    def dump(self):
        """全量确定性快照，用于校验同样输入产生同样状态。"""
        return {
            kind: [self.entities[kind][eid] for eid in sorted(self.entities[kind])]
            for kind in ENTITY_PREFIXES
        }
