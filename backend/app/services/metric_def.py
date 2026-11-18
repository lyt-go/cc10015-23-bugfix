"""指标定义业务规则：状态流转、字段校验与筛选口径都收在这里。"""
from __future__ import annotations

from typing import Any

from app.services.metric_alias_resolution import MetricAliasResolutionService
from app.store import store

MODULE = "metric_def"
LIST_FIELDS = ["指标编号", "指标名称", "计算公式", "统计粒度", "时间窗口", "基准取值", "精度位数", "口径版本"]
REQUIRED_FIELDS = ["指标编号", "指标名称", "计算公式"]
STATUS_ORDER = ["草稿", "待校验", "已生效", "已废弃"]
ACTION_RULES = {"提交校验": "待校验", "标记生效": "已生效", "标记废弃": "已废弃"}
NEGATIVE_ACTIONS = []

resolver = MetricAliasResolutionService()


class MetricDefService:
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
            rows = [row for row in rows if keyword.casefold() in str(row.get("指标编号", "")).casefold()]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        total = len(rows)
        start = (page - 1) * size
        # 跨模块联动：列表带出口径解析记录，和详情页保持同一组数据
        return [self._with_resolution(row) for row in rows[start:start + size]], total

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None
        return self._with_resolution(entry)

    def _with_resolution(self, row: dict[str, Any]) -> dict[str, Any]:
        """读取口径解析记录，标注指标声明的口径版本是否仍是当前计算口径。"""
        data = dict(row)
        metric_code = str(row.get("指标编号") or "").strip()
        resolution = resolver.get_resolution(metric_code) if metric_code else None
        declared = str(row.get("口径版本") or "").strip()
        if resolution is None:
            data["当前口径"] = ""
            data["当前口径版本"] = ""
            data["口径状态"] = "未解析"
        else:
            current_version = str(resolution.get("当前版本编号") or "")
            data["当前口径"] = resolution.get("当前口径", "")
            data["当前口径版本"] = current_version
            data["口径状态"] = "当前" if declared and declared == current_version else "已过期"
        return data

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
            return None, f"指标定义 {entry_id} 不存在或已归档"
        if action not in ACTION_RULES:
            return None, f"动作「{action}」不属于指标定义可执行范围"
        target = ACTION_RULES[action]
        if target not in STATUS_ORDER:
            return None, f"目标状态「{target}」不在允许的状态序列里"
        if target == "已生效":
            # 跨模块联动：拒绝过期版本继续作为当前计算口径
            metric_code = str(entry.get("指标编号") or "").strip()
            resolution = resolver.get_resolution(metric_code) if metric_code else None
            if resolution is not None:
                declared = str(entry.get("口径版本") or "").strip()
                current_version = str(resolution.get("当前版本编号") or "")
                if declared and current_version and declared != current_version:
                    return None, (
                        f"口径版本 {declared} 已过期，指标 {metric_code} 当前计算口径为 "
                        f"{current_version}，请更新口径版本后再标记生效"
                    )
        entry["status"] = target
        entry["pending"] = target != STATUS_ORDER[-1]
        entry["abnormal"] = action in NEGATIVE_ACTIONS
        return entry, f"指标定义已{action}"
