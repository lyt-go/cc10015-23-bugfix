"""口径解析记录：按当前时间重算每个指标的当前口径，并保留回滚历史。

修复要点（对应口径解析记录错位/残留问题）：
- 读取口径版本的新旧口径与生效时间，按当前时间重算解析记录，刷新后不再回到旧解析；
- 生效时间与解析记录同步落库，重新进入页面或返回详情时读到的是同一份记录；
- 同一指标在未跨过生效时间时解析保持原值，跨过生效时间才切换到新记录；
- 重建解析失败时保留上一完整记录，不把指标暂时映射到空口径；
- 解析写入按事务处理，一旦残留便回滚到落库前的记录；
- 旧版本回滚时间与新版本生效时间落在同一天时，按生效时间边界切换并保留同日说明。
"""
from __future__ import annotations

import copy
from datetime import date, datetime
from typing import Any

from app.store import store

MODULE = "metric_alias"
RESOLUTION_MODULE = "metric_alias_resolution"

EFFECTIVE_STATUS = "已生效"
ROLLBACK_STATUS = "已回滚"


def _today() -> date:
    return date.today()


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _parse_day(value: Any) -> date | None:
    """把生效时间/回滚时间/确认时间截成日期；非法值返回 None。"""
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


class MetricAliasResolutionService:
    """维护「指标编号 → 当前口径」的解析记录。"""

    # ---------- 读取（消费端取数） ----------

    def list_resolutions(self, *, now: date | None = None) -> list[dict[str, Any]]:
        """全量口径解析记录：每次读取都按当前时间重算，消费端拿到的始终是最新解析。"""
        return self.resolve_all(now=now)

    def get_resolution(self, metric_code: str, *, now: date | None = None) -> dict[str, Any] | None:
        """单指标解析记录：读前重算，跨过生效时间会切换到新记录。"""
        return self.resolve_metric(metric_code, now=now)

    # ---------- 解析 ----------

    def resolve_all(self, *, now: date | None = None) -> list[dict[str, Any]]:
        metric_codes: list[str] = []
        for row in store.rows(MODULE):
            code = str(row.get("指标编号") or "").strip()
            if code and code not in metric_codes:
                metric_codes.append(code)
        records: list[dict[str, Any]] = []
        for code in metric_codes:
            record = self.resolve_metric(code, now=now)
            if record is not None:
                records.append(record)
        return records

    def resolve_metric(self, metric_code: str, *, now: date | None = None) -> dict[str, Any] | None:
        """按当前时间重算单个指标的解析记录并同步落库。

        - 解析结果与生效时间一起写入，刷新不会读到上一版本缓存；
        - 解析未变化时保留原值（解析时间与历史不动），同一指标刷新保持稳定；
        - 重建失败（没有可用口径或口径为空）时保留上一完整记录，绝不映射到空口径；
        - 写入出错时回滚到落库前的记录，不留事务残留。
        """
        today = now or _today()
        previous = self._find_record(metric_code)
        snapshot = copy.deepcopy(previous)
        try:
            record = self._build_record(metric_code, today, previous)
            if record is None:
                # 重建失败：保留上一完整记录
                return previous
            if previous is not None and self._same_resolution(previous, record):
                return previous
            self._persist(record)
            return record
        except Exception:
            self._restore(metric_code, snapshot)
            return snapshot

    # ---------- 内部 ----------

    def _find_record(self, metric_code: str) -> dict[str, Any] | None:
        for row in store.rows(RESOLUTION_MODULE):
            if row.get("指标编号") == metric_code:
                return row
        return None

    def _versions_of(self, metric_code: str) -> list[dict[str, Any]]:
        return [
            row for row in store.rows(MODULE)
            if str(row.get("指标编号") or "").strip() == metric_code
        ]

    def _build_record(
        self,
        metric_code: str,
        today: date,
        previous: dict[str, Any] | None,
    ) -> dict[str, Any] | None:
        versions = self._versions_of(metric_code)
        if not versions:
            return None
        # 候选：已生效且生效时间已到；按生效时间排序，同日按确认操作次序取舍
        eligible: list[tuple[date, str, int, dict[str, Any]]] = []
        for row in versions:
            if row.get("status") != EFFECTIVE_STATUS:
                continue
            day = _parse_day(row.get("生效时间"))
            if day is None or day > today:
                continue
            eligible.append((day, str(row.get("确认时间") or ""), int(row.get("id", 0)), row))
        if not eligible:
            return None
        eligible.sort(key=lambda item: (item[0], item[1], item[2]))
        current = eligible[-1][3]
        caliber = str(current.get("新口径") or "").strip()
        if not caliber:
            # 空口径视为重建失败，由调用方保留上一完整记录
            return None
        return {
            "id": int(previous.get("id", 0)) if previous else store.next_id(RESOLUTION_MODULE),
            "指标编号": metric_code,
            "当前版本编号": str(current.get("版本编号") or ""),
            "当前口径": caliber,
            "生效时间": str(current.get("生效时间") or ""),
            "解析时间": _now_iso(),
            "解析依据": self._resolve_note(versions, eligible) or "按生效时间解析",
            "历史": self._build_history(versions, current, today),
        }

    def _resolve_note(
        self,
        versions: list[dict[str, Any]],
        eligible: list[tuple[date, str, int, dict[str, Any]]],
    ) -> str:
        """同日冲突说明：同日多版本生效、或回滚时间与生效时间落在同一天。"""
        notes: list[str] = []
        current_day = eligible[-1][0]
        same_day = [row for day, _, _, row in eligible if day == current_day]
        if len(same_day) > 1:
            codes = "、".join(str(row.get("版本编号") or "") for row in same_day)
            notes.append(
                f"{current_day.isoformat()} 同日存在多个生效版本（{codes}），"
                "按生效时间边界解析，同日按确认操作次序取舍"
            )
        effective_days = {
            day for row in versions
            if (day := _parse_day(row.get("生效时间"))) is not None
        }
        for row in versions:
            if row.get("status") != ROLLBACK_STATUS:
                continue
            rollback_day = _parse_day(row.get("回滚时间"))
            if rollback_day is not None and rollback_day in effective_days:
                notes.append(
                    f"版本 {row.get('版本编号')} 的回滚时间与生效时间同日"
                    f"（{rollback_day.isoformat()}），解析记录在时间边界切换"
                )
        return "；".join(notes)

    def _build_history(
        self,
        versions: list[dict[str, Any]],
        current: dict[str, Any],
        today: date,
    ) -> list[dict[str, Any]]:
        """按生效时间先后生成解析历史，回滚版本留档可追溯。"""
        def sort_key(row: dict[str, Any]) -> tuple[date, str, int]:
            day = _parse_day(row.get("生效时间")) or date.max
            return (day, str(row.get("确认时间") or ""), int(row.get("id", 0)))

        history: list[dict[str, Any]] = []
        current_id = current.get("id")
        for row in sorted(versions, key=sort_key):
            status = str(row.get("status") or "")
            day = _parse_day(row.get("生效时间"))
            if status == EFFECTIVE_STATUS and day is not None and day <= today:
                if row.get("id") == current_id:
                    state, reason = "当前口径", "按生效时间解析为当前口径"
                else:
                    state, reason = "已被新版本替代", "被更新版本的生效时间替代"
            elif status == EFFECTIVE_STATUS:
                state = "待生效"
                reason = f"生效时间 {row.get('生效时间')} 未到"
            elif status == ROLLBACK_STATUS:
                state, reason = "已回滚", "执行回滚，退出当前口径"
            else:
                state = status or "草稿"
                reason = "未进入生效流程"
            history.append({
                "版本编号": str(row.get("版本编号") or ""),
                "口径": str(row.get("新口径") or ""),
                "旧口径": str(row.get("旧口径") or ""),
                "生效时间": str(row.get("生效时间") or ""),
                "状态": state,
                "切换原因": reason,
                "确认时间": str(row.get("确认时间") or ""),
                "回滚时间": str(row.get("回滚时间") or ""),
            })
        return history

    def _same_resolution(self, old: dict[str, Any], new: dict[str, Any]) -> bool:
        """解析是否未变化（解析时间不参与比较，保证刷新时保留原值）。"""
        keys = ("指标编号", "当前版本编号", "当前口径", "生效时间", "解析依据", "历史")
        return all(old.get(key) == new.get(key) for key in keys)

    def _persist(self, record: dict[str, Any]) -> None:
        rows = store.rows(RESOLUTION_MODULE)
        for index, row in enumerate(rows):
            if row.get("指标编号") == record.get("指标编号"):
                rows[index] = record
                break
        else:
            rows.append(record)
        store.bump_version(RESOLUTION_MODULE)

    def _restore(self, metric_code: str, snapshot: dict[str, Any] | None) -> None:
        """事务回滚：把解析记录恢复为落库前的快照。"""
        rows = store.rows(RESOLUTION_MODULE)
        for index, row in enumerate(rows):
            if row.get("指标编号") == metric_code:
                if snapshot is None:
                    rows.pop(index)
                else:
                    rows[index] = snapshot
                break
        else:
            if snapshot is not None:
                rows.append(snapshot)
