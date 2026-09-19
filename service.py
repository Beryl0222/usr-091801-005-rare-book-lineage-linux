"""海外古籍研究谱系库的运行入口。"""

import argparse
import json
import os
from http.server import ThreadingHTTPServer

from lineage.api import Handler, SERVICE_ID, SERVICE_NAME, health_payload
from lineage.ops import ingest
from lineage.store import Store

__all__ = ["Handler", "SERVICE_ID", "SERVICE_NAME", "health_payload", "build_store"]

DEFAULT_SEED = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "data", "seed.json")


def build_store(data_path=None, seed_path=None):
    """组装存储：先读持久化数据文件，再幂等导入样例数据。"""
    if data_path and os.path.exists(data_path):
        store = Store.load(data_path)
    else:
        store = Store()
    if seed_path:
        with open(seed_path, encoding="utf-8") as fh:
            ingest(store, json.load(fh))
    return store


def main():
    parser = argparse.ArgumentParser(description=SERVICE_NAME)
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--data", help="JSON 数据文件路径（变更后自动保存）")
    parser.add_argument("--seed", help="启动时幂等导入的样例数据")
    args = parser.parse_args()
    if args.check:
        assert health_payload()["service"] == SERVICE_ID
        store = build_store(seed_path=DEFAULT_SEED)
        assert store.bucket("work"), "样例数据未载入"
        assert store.bucket("license"), "样例许可未载入"
        print("基础检查通过")
        return
    Handler.store = build_store(args.data, args.seed)
    Handler.data_path = args.data
    ThreadingHTTPServer(("0.0.0.0", args.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
