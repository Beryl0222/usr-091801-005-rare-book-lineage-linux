"""HTTP 接口：查询端点与协作操作端点。

所有响应 JSON 键序固定（sort_keys），同样的请求得到字节一致的结果。
"""

import json
import re
import threading
from http.server import BaseHTTPRequestHandler
from urllib.parse import parse_qs, urlparse

from . import ops
from .audit import audit_entries, audit_publication
from .model import CLAIM_KINDS, CLAIM_STATUSES, DomainError, require, require_choice
from .store import Store

SERVICE_ID = "rare-book-lineage"
SERVICE_NAME = "海外古籍研究谱系库"


def health_payload():
    """返回服务身份。"""
    return {"status": "ok", "service": SERVICE_ID, "name": SERVICE_NAME}


# ---------------------------------------------------------------- 视图组装

def _sorted(bucket):
    return sorted(bucket.values(), key=lambda r: r["id"])


def _claims_on(store, subject_type, entity_id):
    out = []
    for claim in store.bucket("claim").values():
        if claim["subject"]["type"] != subject_type:
            continue
        if ops.same_subject(store, claim["subject"],
                            {"type": subject_type, "id": entity_id}):
            out.append(claim)
    return sorted(out, key=lambda c: c["id"])


def _claim_detail(store, claim):
    comments = sorted(
        (c for c in store.bucket("comment").values() if c["claim_id"] == claim["id"]),
        key=lambda c: c["id"],
    )
    return {**claim, "evidence": ops.evidence_of(store, claim["id"]),
            "comments": comments}


def _need(body, *fields):
    require(isinstance(body, dict), "请求体须为 JSON 对象")
    missing = [f for f in fields if body.get(f) in (None, "")]
    require(not missing, f"缺少字段: {', '.join(missing)}")
    return [body[f] for f in fields]


# ---------------------------------------------------------------- 路由表

def _list_view(kind):
    def view(store, match, query, body):
        return {f"{kind}s": _sorted(store.bucket(kind))}
    return view


def _detail_view(kind):
    def view(store, match, query, body):
        return store.get(kind, match.group("id"))
    return view


def _work_detail(store, match, query, body):
    work = store.get("work", match.group("id"))
    editions = [e for e in _sorted(store.bucket("edition"))
                if e["work_id"] == work["id"]]
    return {**work, "editions": editions,
            "claims": _claims_on(store, "work", work["id"])}


def _edition_detail(store, match, query, body):
    edition = store.get("edition", match.group("id"))
    volumes = [v for v in _sorted(store.bucket("volume"))
               if v.get("edition_id") == edition["id"]]
    return {**edition, "work": store.get("work", edition["work_id"]),
            "volumes": volumes,
            "claims": _claims_on(store, "edition", edition["id"])}


def _volume_detail(store, match, query, body):
    volume = store.get("volume", match.group("id"))
    images = [i for i in _sorted(store.bucket("image"))
              if i["volume_id"] == volume["id"]]
    edition = (store.get("edition", volume["edition_id"])
               if volume.get("edition_id") else None)
    return {**volume,
            "institution": store.get("institution", volume["institution_id"]),
            "edition": edition, "images": images,
            "claims": _claims_on(store, "volume", volume["id"])}


def _institution_detail(store, match, query, body):
    inst = store.get("institution", match.group("id"))
    volumes = [v for v in _sorted(store.bucket("volume"))
               if v["institution_id"] == inst["id"]]
    licenses = [l for l in _sorted(store.bucket("license"))
                if l["institution_id"] == inst["id"]]
    return {**inst, "volumes": volumes, "licenses": licenses}


def _image_detail(store, match, query, body):
    image = store.get("image", match.group("id"))
    lic = (store.get("license", image["license_id"])
           if image.get("license_id") else None)
    return {**image, "volume": store.get("volume", image["volume_id"]),
            "license": lic}


def _license_detail(store, match, query, body):
    lic = store.get("license", match.group("id"))
    return {**lic, "institution": store.get("institution", lic["institution_id"])}


def _claims_list(store, match, query, body):
    items = list(store.bucket("claim").values())
    if query.get("subject_type"):
        require_choice(query["subject_type"], ("work", "edition", "volume"),
                       "对象类型")
        items = [c for c in items
                 if c["subject"]["type"] == query["subject_type"]]
    if query.get("subject_id"):
        target = {"type": query.get("subject_type") or "work",
                  "id": query["subject_id"]}
        if not query.get("subject_type"):
            require(False, "按 subject_id 过滤须同时给出 subject_type")
        items = [c for c in items if ops.same_subject(store, c["subject"], target)]
    if query.get("kind"):
        require_choice(query["kind"], CLAIM_KINDS, "观点类型")
        items = [c for c in items if c["kind"] == query["kind"]]
    if query.get("status"):
        require_choice(query["status"], CLAIM_STATUSES, "观点状态")
        items = [c for c in items if c["status"] == query["status"]]
    return {"claims": sorted(items, key=lambda c: c["id"])}


def _claim_detail_view(store, match, query, body):
    return _claim_detail(store, store.get("claim", match.group("id")))


def _events_view(store, match, query, body):
    return {"events": store.events}


# ---------------------------------------------------------------- POST 操作

def _post_institution(store, match, query, body):
    (name,) = _need(body, "name")
    return ops.create_institution(store, name, body.get("city", ""),
                                  body.get("country", ""))


def _post_work(store, match, query, body):
    (title,) = _need(body, "title")
    return ops.create_work(store, title, body.get("aliases"),
                           body.get("note", ""))


def _post_edition(store, match, query, body):
    work_id, label = _need(body, "work_id", "label")
    return ops.create_edition(store, work_id, label, body.get("note", ""))


def _post_volume(store, match, query, body):
    institution_id, shelfmark = _need(body, "institution_id", "shelfmark")
    return ops.create_volume(store, institution_id, shelfmark,
                             edition_id=body.get("edition_id"),
                             completeness=body.get("completeness", "未知"),
                             juan_held=body.get("juan_held"),
                             note=body.get("note", ""))


def _post_image(store, match, query, body):
    volume_id, leaf = _need(body, "volume_id", "leaf")
    return ops.create_image(store, volume_id, leaf,
                            body.get("resolution", "high"),
                            body.get("license_id"))


def _post_license(store, match, query, body):
    institution_id, title, purposes, regions, max_resolution = _need(
        body, "institution_id", "title", "purposes", "regions", "max_resolution")
    return ops.create_license(store, institution_id, title, purposes, regions,
                              max_resolution, body.get("expires_on"))


def _post_claim(store, match, query, body):
    kind, subject_type, subject_id, statement, confidence, author = _need(
        body, "kind", "subject_type", "subject_id", "statement",
        "confidence", "author")
    claim = ops.add_claim(store, kind, subject_type, subject_id, statement,
                          confidence, author, value=body.get("value"),
                          rationale=body.get("rationale", ""),
                          supersedes=body.get("supersedes"))
    for ev in body.get("evidence", []):
        ops.add_evidence(store, claim["id"], image_id=ev.get("image_id"),
                         volume_id=ev.get("volume_id"), leaf=ev.get("leaf"),
                         note=ev.get("note", ""),
                         confidence=ev.get("confidence", "medium"))
    return _claim_detail(store, claim)


def _post_claim_evidence(store, match, query, body):
    claim = store.get("claim", match.group("id"))
    ev = ops.add_evidence(store, claim["id"], image_id=body.get("image_id"),
                          volume_id=body.get("volume_id"), leaf=body.get("leaf"),
                          note=body.get("note", ""),
                          confidence=body.get("confidence", "medium"))
    return ev


def _post_claim_comment(store, match, query, body):
    author, text = _need(body, "author", "body")
    return ops.add_comment(store, match.group("id"), author, text)


def _post_claim_status(store, match, query, body):
    (status,) = _need(body, "status")
    return ops.transition_claim(store, match.group("id"), status,
                                actor=body.get("actor", ""))


def _post_rename(store, match, query, body):
    (name,) = _need(body, "name")
    return ops.rename_institution(store, match.group("id"), name)


def _post_transfer(store, match, query, body):
    (institution_id,) = _need(body, "institution_id")
    return ops.transfer_volume(store, match.group("id"), institution_id,
                               body.get("shelfmark"))


def _post_merge(store, match, query, body):
    entity, left_id, right_id, reason, author = _need(
        body, "entity", "left_id", "right_id", "reason", "author")
    return ops.suggest_merge(store, entity, left_id, right_id, reason, author)


def _post_merge_resolve(store, match, query, body):
    accept, actor = _need(body, "accept", "actor")
    require(isinstance(accept, bool), "accept 须为布尔值")
    return ops.resolve_merge(store, match.group("id"), accept,
                             survivor_id=body.get("survivor_id"), actor=actor)


def _post_ingest(store, match, query, body):
    return ops.ingest(store, body)


def _post_publication(store, match, query, body):
    title, purpose, region, resolution, claim_ids = _need(
        body, "title", "purpose", "region", "resolution", "claim_ids")
    return ops.freeze_publication(store, title, purpose, region, resolution,
                                  claim_ids, image_ids=body.get("image_ids"),
                                  note=body.get("note", ""))


def _post_export(store, match, query, body):
    image_ids, purpose, region, resolution, as_of = _need(
        body, "image_ids", "purpose", "region", "resolution", "as_of")
    return ops.build_export(store, image_ids, purpose, region, resolution, as_of)


def _post_audit(store, match, query, body):
    (as_of,) = _need(body, "as_of")
    if body.get("publication_id"):
        return audit_publication(store, body["publication_id"], as_of,
                                 purpose=body.get("purpose"),
                                 region=body.get("region"),
                                 resolution=body.get("resolution"))
    entries, purpose, region, resolution = _need(
        body, "entries", "purpose", "region", "resolution")
    return audit_entries(store, entries, purpose, region, resolution, as_of)


ROUTES = [
    ("GET", r"/api/works", _list_view("work"), False),
    ("GET", r"/api/works/(?P<id>[^/]+)", _work_detail, False),
    ("GET", r"/api/editions", _list_view("edition"), False),
    ("GET", r"/api/editions/(?P<id>[^/]+)", _edition_detail, False),
    ("GET", r"/api/volumes", _list_view("volume"), False),
    ("GET", r"/api/volumes/(?P<id>[^/]+)", _volume_detail, False),
    ("GET", r"/api/institutions", _list_view("institution"), False),
    ("GET", r"/api/institutions/(?P<id>[^/]+)", _institution_detail, False),
    ("GET", r"/api/images", _list_view("image"), False),
    ("GET", r"/api/images/(?P<id>[^/]+)", _image_detail, False),
    ("GET", r"/api/licenses", _list_view("license"), False),
    ("GET", r"/api/licenses/(?P<id>[^/]+)", _license_detail, False),
    ("GET", r"/api/claims", _claims_list, False),
    ("GET", r"/api/claims/(?P<id>[^/]+)", _claim_detail_view, False),
    ("GET", r"/api/merges", _list_view("merge"), False),
    ("GET", r"/api/publications", _list_view("publication"), False),
    ("GET", r"/api/publications/(?P<id>[^/]+)", _detail_view("publication"), False),
    ("GET", r"/api/events", _events_view, False),
    ("POST", r"/api/institutions", _post_institution, True),
    ("POST", r"/api/works", _post_work, True),
    ("POST", r"/api/editions", _post_edition, True),
    ("POST", r"/api/volumes", _post_volume, True),
    ("POST", r"/api/images", _post_image, True),
    ("POST", r"/api/licenses", _post_license, True),
    ("POST", r"/api/claims", _post_claim, True),
    ("POST", r"/api/claims/(?P<id>[^/]+)/evidence", _post_claim_evidence, True),
    ("POST", r"/api/claims/(?P<id>[^/]+)/comments", _post_claim_comment, True),
    ("POST", r"/api/claims/(?P<id>[^/]+)/status", _post_claim_status, True),
    ("POST", r"/api/institutions/(?P<id>[^/]+)/rename", _post_rename, True),
    ("POST", r"/api/volumes/(?P<id>[^/]+)/transfer", _post_transfer, True),
    ("POST", r"/api/merges", _post_merge, True),
    ("POST", r"/api/merges/(?P<id>[^/]+)/resolve", _post_merge_resolve, True),
    ("POST", r"/api/ingest", _post_ingest, True),
    ("POST", r"/api/publications", _post_publication, True),
    ("POST", r"/api/exports/evaluate", _post_export, False),
    ("POST", r"/api/audits", _post_audit, False),
]

COMPILED_ROUTES = [(method, re.compile(pattern), fn, mutates)
                   for method, pattern, fn, mutates in ROUTES]


class Handler(BaseHTTPRequestHandler):
    """谱系库 API 处理器；store 由入口在启动时注入。"""

    store = Store()
    data_path = None
    _lock = threading.RLock()

    def do_GET(self):
        self._handle("GET")

    def do_POST(self):
        self._handle("POST")

    def _handle(self, method):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        try:
            if method == "GET" and path == "/health":
                return self._send(200, health_payload())
            query = {k: v[0] for k, v in parse_qs(parsed.query).items()}
            body = self._read_body() if method == "POST" else None
            with self._lock:
                for route_method, pattern, fn, mutates in COMPILED_ROUTES:
                    if route_method != method:
                        continue
                    match = pattern.fullmatch(path)
                    if not match:
                        continue
                    result = fn(self.store, match, query, body)
                    if mutates and self.data_path:
                        self.store.save(self.data_path)
                    return self._send(200, result)
            self._send(404, {"error": f"路径不存在: {method} {path}"})
        except DomainError as err:
            self._send(400, {"error": str(err)})
        except KeyError as err:
            self._send(404, {"error": str(err.args[0]) if err.args else "未找到"})
        except json.JSONDecodeError as err:
            self._send(400, {"error": f"JSON 解析失败: {err}"})

    def _read_body(self):
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""
        if not raw:
            return {}
        return json.loads(raw.decode("utf-8"))

    def _send(self, status, obj):
        body = json.dumps(obj, ensure_ascii=False, sort_keys=True).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args):
        return
