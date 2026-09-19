"""内存存储：实体表、事件日志与确定性编号。

- 编号按类型递增（W-0001、CL-0001……），同样的操作序列得到同样的编号。
- 所有变更写入事件日志，馆藏更名、卷册迁移、实体合并都靠事件维系谱系。
- 合并不删除实体，只留 merged_into 墓碑；get() 默认沿墓碑解析到存续实体。
"""

import json

from .model import DomainError

ENTITY_KINDS = (
    "work",
    "edition",
    "volume",
    "institution",
    "image",
    "license",
    "claim",
    "evidence",
    "comment",
    "merge",
    "publication",
)

ID_PREFIXES = {
    "work": "W",
    "edition": "ED",
    "volume": "VOL",
    "institution": "INST",
    "image": "IMG",
    "license": "LIC",
    "claim": "CL",
    "evidence": "EV",
    "comment": "CMT",
    "merge": "MG",
    "publication": "PUB",
}


class Store:
    def __init__(self):
        self.entities = {kind: {} for kind in ENTITY_KINDS}
        self.events = []
        self.counters = {}

    def bucket(self, kind):
        return self.entities[kind]

    def new_id(self, kind):
        prefix = ID_PREFIXES[kind]
        next_value = self.counters.get(prefix, 0) + 1
        self.counters[prefix] = next_value
        return f"{prefix}-{next_value:04d}"

    def add(self, kind, record):
        self.bucket(kind)[record["id"]] = record
        return record

    def get(self, kind, entity_id, follow_merges=True):
        record = self.bucket(kind).get(entity_id)
        if record is None:
            raise KeyError(f"{kind} 不存在: {entity_id}")
        seen = set()
        while follow_merges and record.get("merged_into"):
            if record["id"] in seen:
                raise DomainError(f"{kind} 合并链存在循环: {entity_id}")
            seen.add(record["id"])
            target = self.bucket(kind).get(record["merged_into"])
            if target is None:
                raise DomainError(f"{kind} 合并目标缺失: {record['merged_into']}")
            record = target
        return record

    def log(self, event_type, **payload):
        seq = len(self.events) + 1
        self.events.append({"seq": seq, "type": event_type, **payload})
        return seq

    def to_dict(self):
        return {
            "entities": self.entities,
            "events": self.events,
            "counters": self.counters,
        }

    @classmethod
    def from_dict(cls, data):
        store = cls()
        for kind in ENTITY_KINDS:
            store.entities[kind] = dict(data.get("entities", {}).get(kind, {}))
        store.events = list(data.get("events", []))
        store.counters = dict(data.get("counters", {}))
        return store

    def save(self, path):
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(self.to_dict(), fh, ensure_ascii=False, sort_keys=True, indent=2)
            fh.write("\n")

    @classmethod
    def load(cls, path):
        with open(path, encoding="utf-8") as fh:
            return cls.from_dict(json.load(fh))
