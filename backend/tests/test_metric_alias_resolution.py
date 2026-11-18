"""口径解析记录链路测试：按当前时间重算、同步落库、回滚留档、同日冲突与跨模块联动。"""
import sys, os
from datetime import date, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from app.store import store
from app.services.metric_alias import MetricAliasService
from app.services.metric_alias_resolution import (
    RESOLUTION_MODULE,
    MetricAliasResolutionService,
)
from app.services.metric_def import MetricDefService

TODAY = date.today()
YESTERDAY = (TODAY - timedelta(days=1)).isoformat()
TODAY_ISO = TODAY.isoformat()
FUTURE = (TODAY + timedelta(days=365)).isoformat()


def _clear_all():
    for k in list(store._tables.keys()):
        store._tables[k] = []


def _make_alias(ma, code, metric, new_caliber, effective, old_caliber="old_expr"):
    entry, missing = ma.create_entry({
        "版本编号": code,
        "指标编号": metric,
        "变更说明": f"{code} 变更",
        "旧口径": old_caliber,
        "新口径": new_caliber,
        "生效时间": effective,
        "审批状态": "草稿",
        "变更人": "tester",
    })
    assert entry is not None, missing
    return entry


def _confirm(ma, entry):
    result, message = ma.run_action(int(entry["id"]), "确认生效")
    assert result is not None, message
    return result


def test_resolution_persisted_on_confirm():
    """确认生效后解析记录同步落库：当前口径、生效时间与版本一致。"""
    _clear_all()
    ma = MetricAliasService()
    entry = _make_alias(ma, "ALIAS-A1", "M-A", "clicks/valid_impressions", TODAY_ISO)
    _confirm(ma, entry)
    records = store.rows(RESOLUTION_MODULE)
    assert len(records) == 1, "确认生效后应同步落库一条解析记录"
    record = records[0]
    assert record["指标编号"] == "M-A"
    assert record["当前版本编号"] == "ALIAS-A1"
    assert record["当前口径"] == "clicks/valid_impressions"
    assert record["生效时间"] == TODAY_ISO, "生效时间必须与解析记录同步落库"


def test_resolution_switches_after_effective_time_and_keeps_stable():
    """跨过生效时间切换到新记录；未跨过时刷新保持原值。"""
    _clear_all()
    ma = MetricAliasService()
    resolver = MetricAliasResolutionService()
    v1 = _make_alias(ma, "ALIAS-B1", "M-B", "caliber_v1", YESTERDAY)
    _confirm(ma, v1)
    # 生效时间未到：解析停留在 v1，且重复刷新保持原值
    v2 = _make_alias(ma, "ALIAS-B2", "M-B", "caliber_v2", FUTURE)
    _confirm(ma, v2)
    first = resolver.get_resolution("M-B")
    assert first["当前版本编号"] == "ALIAS-B1"
    second = resolver.get_resolution("M-B")
    assert second is first, "同一指标未跨过生效时间时刷新应保留原值"
    # 跨过生效时间：切到新记录，且之后同一时间的刷新不再回到旧解析
    crossed_day = date.fromisoformat(FUTURE)
    crossed = resolver.resolve_metric("M-B", now=crossed_day)
    assert crossed["当前版本编号"] == "ALIAS-B2"
    assert crossed["当前口径"] == "caliber_v2"
    assert crossed["生效时间"] == FUTURE
    again = resolver.resolve_metric("M-B", now=crossed_day)
    assert again["当前版本编号"] == "ALIAS-B2", "切换后刷新不应回到旧解析"
    later = resolver.resolve_metric("M-B", now=crossed_day + timedelta(days=1))
    assert later["当前版本编号"] == "ALIAS-B2", "跨过生效时间后当前口径保持稳定"


def test_old_caliber_traceable_in_history():
    """切换到新版本后旧口径仍可追溯。"""
    _clear_all()
    ma = MetricAliasService()
    resolver = MetricAliasResolutionService()
    v1 = _make_alias(ma, "ALIAS-C1", "M-C", "caliber_old", YESTERDAY, old_caliber="caliber_seed")
    _confirm(ma, v1)
    v2 = _make_alias(ma, "ALIAS-C2", "M-C", "caliber_new", TODAY_ISO)
    _confirm(ma, v2)
    record = resolver.get_resolution("M-C")
    assert record["当前版本编号"] == "ALIAS-C2"
    history = {item["版本编号"]: item for item in record["历史"]}
    assert history["ALIAS-C1"]["口径"] == "caliber_old"
    assert history["ALIAS-C1"]["旧口径"] == "caliber_seed"
    assert history["ALIAS-C1"]["状态"] == "已被新版本替代"
    assert history["ALIAS-C2"]["状态"] == "当前口径"


def test_rollback_restores_previous_and_keeps_history():
    """执行回滚后当前口径回退到上一版本，回滚历史保留。"""
    _clear_all()
    ma = MetricAliasService()
    resolver = MetricAliasResolutionService()
    v1 = _make_alias(ma, "ALIAS-D1", "M-D", "caliber_v1", YESTERDAY)
    _confirm(ma, v1)
    v2 = _make_alias(ma, "ALIAS-D2", "M-D", "caliber_v2", TODAY_ISO)
    _confirm(ma, v2)
    assert resolver.get_resolution("M-D")["当前版本编号"] == "ALIAS-D2"
    result, message = ma.run_action(int(v2["id"]), "执行回滚")
    assert result is not None, message
    record = resolver.get_resolution("M-D")
    assert record["当前版本编号"] == "ALIAS-D1", "回滚后应回退到上一完整记录"
    assert record["当前口径"] == "caliber_v1"
    history = {item["版本编号"]: item for item in record["历史"]}
    assert history["ALIAS-D2"]["状态"] == "已回滚", "回滚历史必须留档"
    assert history["ALIAS-D2"]["回滚时间"] == TODAY_ISO


def test_same_day_conflict_resolved_by_effective_time_with_note():
    """同日多版本生效：按生效时间边界解析、同日按确认次序取舍，并保留同日说明。"""
    _clear_all()
    ma = MetricAliasService()
    resolver = MetricAliasResolutionService()
    v1 = _make_alias(ma, "ALIAS-E1", "M-E", "caliber_first", TODAY_ISO)
    _confirm(ma, v1)
    v2 = _make_alias(ma, "ALIAS-E2", "M-E", "caliber_second", TODAY_ISO)
    _confirm(ma, v2)
    record = resolver.get_resolution("M-E")
    assert record["当前版本编号"] == "ALIAS-E2", "同日生效时按确认操作次序取舍"
    assert "同日" in record["解析依据"], "同日冲突必须保留同日说明"
    # 旧版本回滚时间与新版本生效时间同日：同样留同日说明
    result, _ = ma.run_action(int(v2["id"]), "执行回滚")
    assert result is not None
    record = resolver.get_resolution("M-E")
    assert record["当前版本编号"] == "ALIAS-E1"
    assert "同日" in record["解析依据"]


def test_rebuild_failure_keeps_previous_complete_record():
    """重建解析失败时保留上一完整记录，不把指标映射到空口径。"""
    _clear_all()
    ma = MetricAliasService()
    resolver = MetricAliasResolutionService()
    v1 = _make_alias(ma, "ALIAS-F1", "M-F", "caliber_v1", YESTERDAY)
    _confirm(ma, v1)
    assert resolver.get_resolution("M-F")["当前口径"] == "caliber_v1"
    # 模拟脏数据：唯一生效版本的新口径被清空
    v1["新口径"] = "   "
    record = resolver.get_resolution("M-F")
    assert record is not None, "重建失败时不得丢弃上一完整记录"
    assert record["当前口径"] == "caliber_v1", "不能把指标暂时映射到空口径"
    # 全部版本被误删时同样保留上一完整记录
    store._tables["metric_alias"] = []
    record = resolver.get_resolution("M-F")
    assert record is not None and record["当前口径"] == "caliber_v1"


def test_metric_def_rejects_expired_caliber_version():
    """跨模块联动：指标定义读取解析记录，拒绝过期版本作为当前计算口径。"""
    _clear_all()
    ma = MetricAliasService()
    md = MetricDefService()
    v1 = _make_alias(ma, "ALIAS-G1", "M-G", "caliber_current", YESTERDAY)
    _confirm(ma, v1)
    entry, missing = md.create_entry({
        "指标编号": "M-G",
        "指标名称": "点击率",
        "计算公式": "clicks / impressions",
        "口径版本": "ALIAS-OLD",
    })
    assert entry is not None, missing
    result, message = md.run_action(int(entry["id"]), "标记生效")
    assert result is None, "过期口径版本不得继续作为当前计算口径"
    assert "已过期" in message and "ALIAS-G1" in message
    # 列表与详情带出口径状态，且两者一致
    items, total = md.list_entries()
    assert total == 1
    assert items[0]["口径状态"] == "已过期"
    assert items[0]["当前口径版本"] == "ALIAS-G1"
    detail = md.get_entry(int(entry["id"]))
    assert detail["口径状态"] == items[0]["口径状态"]
    assert detail["当前口径"] == items[0]["当前口径"]
    # 更新为当前版本后允许标记生效
    entry["口径版本"] = "ALIAS-G1"
    result, message = md.run_action(int(entry["id"]), "标记生效")
    assert result is not None, message
    assert result["status"] == "已生效"


def test_resolve_all_returns_full_set_for_consumers():
    """结果确认后消费端可取得全量口径解析记录。"""
    _clear_all()
    ma = MetricAliasService()
    resolver = MetricAliasResolutionService()
    _confirm(ma, _make_alias(ma, "ALIAS-H1", "M-H1", "caliber_h1", YESTERDAY))
    _confirm(ma, _make_alias(ma, "ALIAS-H2", "M-H2", "caliber_h2", TODAY_ISO))
    _make_alias(ma, "ALIAS-H3", "M-H3", "caliber_h3", TODAY_ISO)  # 草稿，不产生解析
    records = resolver.list_resolutions()
    by_metric = {record["指标编号"]: record for record in records}
    assert set(by_metric) == {"M-H1", "M-H2"}, "只有已生效且生效时间已到的版本才产生解析记录"
    assert by_metric["M-H2"]["当前口径"] == "caliber_h2"
    assert store.rows(RESOLUTION_MODULE), "全量解析必须同步落库"
