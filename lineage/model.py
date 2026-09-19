"""领域常量、错误与校验。"""

import re

DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

# 观点类型：断代 / 作者 / 套印技法 / 流传路径 / 归属
CLAIM_KINDS = ("dating", "authorship", "technique", "transmission", "attribution")
CLAIM_KIND_LABELS = {
    "dating": "断代",
    "authorship": "作者",
    "technique": "套印技法",
    "transmission": "流传路径",
    "attribution": "归属",
}

# 观点状态：草拟 → 讨论中 → 采用；任何未删除状态可撤回，撤回为终态
CLAIM_STATUSES = ("draft", "discussion", "adopted", "retracted")
STATUS_LABELS = {
    "draft": "草拟",
    "discussion": "讨论中",
    "adopted": "采用",
    "retracted": "已撤回",
}
ALLOWED_TRANSITIONS = {
    "draft": {"discussion", "retracted"},
    "discussion": {"draft", "adopted", "retracted"},
    "adopted": {"discussion", "retracted"},
    "retracted": set(),
}

# 置信程度（观点与证据共用）
CONFIDENCE_LEVELS = ("low", "medium", "high")
CONFIDENCE_LABELS = {"low": "低", "medium": "中", "high": "高"}

# 清晰度档位，可比较大小
RESOLUTION_TIERS = {"low": 1, "medium": 2, "high": 3}

COMPLETENESS = ("完整", "残卷", "未知")

# 观点可挂接的对象
SUBJECT_TYPES = ("work", "edition", "volume")

# 允许合并建议的实体（合并留墓碑，不删除）
MERGEABLE_ENTITIES = ("work", "edition", "volume")


class DomainError(ValueError):
    """业务规则校验失败。"""


def require(condition, message):
    if not condition:
        raise DomainError(message)


def require_choice(value, choices, field):
    require(value in choices, f"{field} 取值无效: {value!r}，可选: {list(choices)}")


def require_date(value, field):
    require(
        isinstance(value, str) and bool(DATE_RE.match(value)),
        f"{field} 须为 YYYY-MM-DD 格式: {value!r}",
    )
