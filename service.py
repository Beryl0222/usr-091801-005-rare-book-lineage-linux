"""海外古籍研究谱系库的 HTTP 服务入口。

所有响应均为键序固定的 JSON；导出检查与出版审计是纯函数式接口，
同样输入重复请求必然得到逐字节一致的结果。
"""

import argparse
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

from domain import COLLECTIONS, DomainError, Store
from seed import build_seed

SERVICE_ID = "rare-book-lineage"
SERVICE_NAME = "海外古籍研究谱系库"


def health_payload():
    """返回服务身份。"""
    return {"status": "ok", "service": SERVICE_ID, "name": SERVICE_NAME}


def canonical_json(payload):
    return json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")


class Service(ThreadingHTTPServer):
    """携带领域数据源的 HTTP 服务。"""

    daemon_threads = True

    def __init__(self, address, store):
        self.store = store
        super().__init__(address, Handler)


class Handler(BaseHTTPRequestHandler):
    """只负责 HTTP 与领域层之间的翻译。"""

    server_version = "RareBookLineage/1"

    # ---------- 基础工具 ----------

    @property
    def store(self):
        return getattr(self.server, "store", None)

    def _send(self, payload, status=200):
        body = canonical_json(payload)
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _body(self):
        length = int(self.headers.get("Content-Length") or 0)
        if length == 0:
            return {}
        try:
            data = json.loads(self.rfile.read(length).decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            raise DomainError("请求体不是合法 JSON")
        if not isinstance(data, dict):
            raise DomainError("请求体必须是 JSON 对象")
        return data

    def _require_store(self):
        if self.store is None:
            raise DomainError("服务未挂载数据源", 503)
        return self.store

    def _dispatch(self, routes):
        try:
            parts = [p for p in urlsplit(self.path).path.split("/") if p]
            query = {k: v[0] for k, v in parse_qs(urlsplit(self.path).query).items()}
            payload, status = routes(parts, query)
            self._send(payload, status)
        except DomainError as error:
            self._send({"error": str(error)}, error.status)
        except Exception:  # noqa: BLE001 - 不暴露内部细节
            self._send({"error": "内部错误"}, 500)

    # ---------- GET ----------

    def do_GET(self):
        self._dispatch(self._get)

    def _get(self, parts, query):
        if not parts:
            raise DomainError("未找到资源", 404)
        if parts == ["health"]:
            return health_payload(), 200

        known = (
            (len(parts) == 1 and parts[0] in COLLECTIONS)
            or (len(parts) == 2 and parts[0] in COLLECTIONS)
            or (len(parts) == 3 and parts[0] in ("volumes", "claims", "publications"))
        )
        if not known:
            raise DomainError("未找到资源", 404)
        store = self._require_store()

        if len(parts) == 1 and parts[0] in COLLECTIONS:
            kind = COLLECTIONS[parts[0]]
            return {"items": store.list(kind)}, 200

        if len(parts) == 2 and parts[0] in COLLECTIONS:
            kind = COLLECTIONS[parts[0]]
            return store.view(kind, parts[1]), 200

        if len(parts) == 3 and parts[0] == "volumes" and parts[2] == "lineage":
            return store.volume_lineage(parts[1]), 200

        if len(parts) == 3 and parts[0] == "claims" and parts[2] == "comments":
            claim = store.get("claim", parts[1])
            comments = [
                c for c in store.list("comment") if c["claim_id"] == claim["id"]
            ]
            comments.sort(key=lambda c: c["seq"])
            return {"items": comments}, 200

        if len(parts) == 3 and parts[0] == "publications" and parts[2] == "audit":
            as_of = query.get("as_of")
            if not as_of:
                raise DomainError("审计必须显式给出 as_of 日期以保证可复现")
            report = store.audit_publication(
                parts[1],
                usage=query.get("usage", "publication"),
                region=query.get("region", "WORLD"),
                resolution=query.get("resolution", "high"),
                as_of=as_of,
            )
            return report, 200

        raise DomainError("未找到资源", 404)

    # ---------- POST ----------

    def do_POST(self):
        self._dispatch(self._post)

    def _post(self, parts, query):
        store = self._require_store()
        body = self._body()

        if parts == ["import"]:
            return store.import_bundle(body), 200

        if parts == ["exports", "check"]:
            as_of = body.get("as_of")
            if not as_of:
                raise DomainError("导出检查必须显式给出 as_of 日期以保证可复现")
            report = store.export_check(
                body.get("image_ids", []),
                usage=body.get("usage", "publication"),
                region=body.get("region", "WORLD"),
                resolution=body.get("resolution", "high"),
                as_of=as_of,
            )
            return report, 200

        if len(parts) == 1 and parts[0] in COLLECTIONS:
            return self._create(store, COLLECTIONS[parts[0]], body)

        if len(parts) == 3 and parts[0] == "claims" and parts[2] == "transition":
            claim = store.transition_claim(parts[1], body.get("to", ""))
            return {"item": claim}, 200

        if len(parts) == 3 and parts[0] == "claims" and parts[2] == "comments":
            kid = store.add_comment(parts[1], body.get("author", ""), body.get("body", ""))
            return {"id": kid, "item": store.view("comment", kid)}, 201

        if len(parts) == 3 and parts[0] == "merges" and parts[2] == "resolve":
            merge = store.resolve_merge(parts[1], bool(body.get("accept")))
            return {"item": merge}, 200

        if len(parts) == 3 and parts[0] == "institutions" and parts[2] == "rename":
            inst = store.rename_institution(
                parts[1], body.get("name", ""), body.get("note", "机构更名")
            )
            return {"item": store.view("institution", inst["id"])}, 200

        if len(parts) == 3 and parts[0] == "volumes" and parts[2] == "migrate":
            volume = store.migrate_volume(
                parts[1], body.get("institution_id", ""), body.get("note", "卷册迁移")
            )
            return {"item": store.view("volume", volume["id"])}, 200

        raise DomainError("未找到资源", 404)

    def _create(self, store, kind, body):
        if kind == "work":
            rid, created = store.add_work(body.get("title", ""), body.get("aliases", []))
        elif kind == "edition":
            rid, created = store.add_edition(
                body.get("work_id", ""), body.get("statement", ""), body.get("description", "")
            )
        elif kind == "volume":
            rid, created = store.add_volume(
                body.get("edition_id", ""), body.get("institution_id", ""),
                body.get("shelfmark", ""), body.get("fragment", False), body.get("label", ""),
            )
        elif kind == "institution":
            rid, created = store.add_institution(body.get("name", ""), body.get("aliases", []))
        elif kind == "image":
            rid, created = store.add_image(
                body.get("volume_id", ""), body.get("leaf", ""),
                body.get("license_id"), body.get("note", ""),
            )
        elif kind == "license":
            rid, created = store.add_license(
                body.get("institution_id", ""), body.get("name", ""),
                body.get("usages", []), body.get("regions", []),
                body.get("max_resolution", ""), body.get("expires_on"),
            )
        elif kind == "claim":
            rid = store.add_claim(
                body.get("kind", ""), body.get("subject"), body.get("statement", ""),
                body.get("confidence", ""), body.get("evidence", []),
                body.get("author", ""), body.get("revises"),
            )
            return {"id": rid, "item": store.view("claim", rid)}, 201
        elif kind == "merge":
            rid = store.add_merge(
                body.get("kind", ""), body.get("source_id", ""),
                body.get("target_id", ""), body.get("reason", ""),
            )
            return {"id": rid, "item": store.view("merge", rid)}, 201
        elif kind == "publication":
            rid = store.create_publication(
                body.get("title", ""), body.get("claim_ids", []), body.get("note", "")
            )
            return {"id": rid, "item": store.view("publication", rid)}, 201
        else:
            raise DomainError(f"该资源不支持创建：{kind}", 404)
        return {"id": rid, "created": created, "item": store.view(kind, rid)}, 201

    def log_message(self, *_args):
        return


def self_check():
    """基础检查：样例库可构建，导出与审计结果确定。"""
    store, ids = build_seed(Store())
    assert health_payload()["service"] == SERVICE_ID
    assert len(store.entities["volume"]) == 5
    audit_args = dict(usage="publication", region="WORLD", resolution="high", as_of="2026-09-19")
    first = store.audit_publication(ids["publication"], **audit_args)
    second = store.audit_publication(ids["publication"], **audit_args)
    assert canonical_json(first) == canonical_json(second)
    export_args = dict(usage="exhibition", region="GB", resolution="medium", as_of="2026-09-19")
    assert canonical_json(store.export_check(ids["images"], **export_args)) == canonical_json(
        store.export_check(ids["images"], **export_args)
    )
    print("基础检查通过")


def main():
    parser = argparse.ArgumentParser(description=SERVICE_NAME)
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--empty", action="store_true", help="不载入样例数据")
    args = parser.parse_args()
    if args.check:
        self_check()
        return
    store = Store()
    if not args.empty:
        build_seed(store)
    print(f"{SERVICE_NAME} 已启动：http://0.0.0.0:{args.port}")
    Service(("0.0.0.0", args.port), store).serve_forever()


if __name__ == "__main__":
    main()
