"""口径注册业务规则：状态流转、字段校验与筛选口径都收在这里。"""
from __future__ import annotations

import copy
from datetime import date, datetime
from typing import Any

from app.services.metric_alias_resolution import MetricAliasResolutionService
from app.store import store

MODULE = "metric_alias"
LIST_FIELDS = ["版本编号", "指标编号", "变更说明", "旧口径", "新口径", "生效时间", "审批状态", "变更人"]
REQUIRED_FIELDS = ["版本编号", "指标编号", "变更说明"]
STATUS_ORDER = ["草稿", "待审批", "已生效", "已回滚"]
ACTION_RULES = {"提交审批": "待审批", "确认生效": "已生效", "执行回滚": "已回滚"}
NEGATIVE_ACTIONS = []

resolver = MetricAliasResolutionService()


class MetricAliasService:
    def list_entries(
        self,
        *,
        filters: dict[str, str] | None = None,
        keyword: str | None = None,
        status: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = store.rows(MODULE)
        for field, expected in (filters or {}).items():
            rows = [row for row in rows if expected.casefold() in str(row.get(field, "")).casefold()]
        if keyword:
            rows = [row for row in rows if keyword.casefold() in str(row.get("版本编号", "")).casefold()]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        total = len(rows)
        start = (page - 1) * size
        return rows[start:start + size], total

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        return store.find(MODULE, entry_id)

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        missing = [field for field in REQUIRED_FIELDS if not str(values.get(field) or "").strip()]
        if missing:
            return None, missing
        rows = store.rows(MODULE)
        entry = {"id": max((int(row.get("id", 0)) for row in rows), default=0) + 1}
        entry.update({field: values.get(field) for field in LIST_FIELDS})
        entry["status"] = STATUS_ORDER[0]
        entry["pending"] = True
        entry["abnormal"] = False
        rows.append(entry)
        return entry, []

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"口径版本 {entry_id} 不存在或已归档"
        if action not in ACTION_RULES:
            return None, f"动作「{action}」不属于口径注册可执行范围"
        target = ACTION_RULES[action]
        if target not in STATUS_ORDER:
            return None, f"目标状态「{target}」不在允许的状态序列里"
        # 状态、时间戳与口径解析记录在同一事务里落库，任何一步残留都整体回滚
        snapshot = copy.deepcopy(entry)
        try:
            entry["status"] = target
            entry["pending"] = target != STATUS_ORDER[-1]
            entry["abnormal"] = action in NEGATIVE_ACTIONS
            if action == "确认生效":
                entry["确认时间"] = datetime.now().isoformat(timespec="seconds")
            if action == "执行回滚":
                entry["回滚时间"] = date.today().isoformat()
            metric_code = str(entry.get("指标编号") or "").strip()
            if metric_code and target in ("已生效", "已回滚"):
                # 生效时间与解析记录同步落库，刷新后不会读到上一版本缓存
                resolver.resolve_metric(metric_code)
        except Exception:
            entry.clear()
            entry.update(snapshot)
            return None, f"口径版本{action}失败，解析事务已回滚"
        return entry, f"口径版本已{action}"
