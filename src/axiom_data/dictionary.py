"""Read-only fact dictionary generated from adapters and Snapshot contracts.

Types, units, mappings and availability rules have no duplicate schema here.
Chinese meanings are presentation annotations; unknown extensions keep their
field name. Feature formulas belong to Research, not this fact dictionary.
"""
from __future__ import annotations

from copy import deepcopy

from .protocols import QueryError


_MEANINGS = {
    "security_id": "稳定证券身份", "source_code": "供应商证券代码",
    "session": "交易归属日", "exchange": "交易所", "name": "证券名称",
    "asset_type": "资产类别", "open": "未复权开盘价", "high": "未复权最高价",
    "low": "未复权最低价", "close": "未复权收盘价（指数域为指数点位）",
    "pre_close": "供应商前收盘价", "volume_shares": "成交股数",
    "volume_units": "成交基金份额", "amount_cny": "成交金额",
    "factor": "供应商累计复权因子；调整价使用因子比", "is_open": "交易所是否开市",
    "listing_date": "上市日期", "delisting_date": "规范退市有效边界，空值不证明永不退市",
    "vendor_delist_date": "供应商原始退市日期", "list_status": "供应商上市状态",
    "vendor_list_status": "供应商原始上市状态", "event_type": "上市/退市事件类别",
    "event_date": "事件经济日期", "event_state": "事件状态",
    "is_suspended": "全天停牌标志；日内或矛盾事件可为未知",
    "suspend_timing": "供应商停复牌时间段", "status_reason": "停复牌归纳原因",
    "universe_id": "成员池身份", "membership_id": "成员区间身份",
    "effective_from": "成员区间起点，含当日", "effective_to": "成员区间终点，不含当日",
    "weight_percent": "供应商指数权重", "is_member": "指定 session 的 PIT 成员投影",
    "endpoint": "财务来源接口", "report_type": "供应商报表类型",
    "report_period": "业绩归属报告期末", "announcement_date": "供应商公告日",
    "actual_announcement_date": "来源声明的实际公告日，不是本系统接收时刻",
    "basis": "来源累计/报表口径", "total_revenue": "营业总收入",
    "parent_net_income": "归母净利润", "total_assets": "资产总计",
    "total_liabilities": "负债合计", "parent_equity": "归母股东权益",
    "operating_cash_flow": "经营活动现金流量净额", "investing_cash_flow": "投资活动现金流量净额",
    "financing_cash_flow": "筹资活动现金流量净额", "roe": "净资产收益率",
    "weighted_roe": "加权净资产收益率", "debt_to_assets": "资产负债率",
    "process_status": "分红原生过程状态；预案与实施分别保存",
    "cash_dividend_before_tax_per_share": "每股税前现金分红", "cash_dividend_per_unit": "每基金份额现金分红",
    "bonus_shares_per_share": "每股送股比例", "capital_transfer_shares_per_share": "每股转增比例",
    "implementation_announcement_date": "分红实施公告日", "record_date": "股权登记日",
    "ex_date": "除权除息日", "pay_date": "派息日", "holders": "原生股东整组 JSON",
    "holder_count": "本响应股东组人数", "group_completeness": "相对供应商响应的整组完整状态",
    "group_issue": "股东组缺失/重复原因", "top10_ratio": "本组前十股东持股比例之和",
    "up_limit": "涨停价，不证明可成交", "down_limit": "跌停价，不证明可成交",
    "revision_id": "内容修订身份", "revision_sequence": "声明的修订次序",
    "first_observed_at": "本系统首次实际接收本修订的时刻", "raw_batch_id": "实际 Raw 接收记录引用",
    "source_available_at": "修订绑定的市场公开时刻，须有证据", "evidence_ref": "修订公开时刻证据引用",
}
_TIME_ROLES = {
    "session": "交易日", "report_period": "业绩报告期",
    "announcement_date": "供应商披露日", "actual_announcement_date": "供应商披露日",
    "implementation_announcement_date": "供应商实施披露日", "first_observed_at": "系统知识时间",
    "source_available_at": "有证据的市场知识时间", "effective_from": "经济有效起点（含）",
    "effective_to": "经济有效终点（不含）",
}


def _catalogue():
    from . import event_sources, provider_etf, provider_local, vendor_listing, vendor_membership
    identity = {"000001.SZ": "dictionary-placeholder"}
    for module in (provider_local, provider_etf):
        for endpoint, contract in module.CONTRACTS.items():
            yield module.DOMAINS[endpoint], endpoint, contract, module.profile_for(endpoint, identity_map=identity)
    for endpoint, contract in event_sources.CONTRACTS.items():
        if endpoint.endswith("_vip"):
            continue  # VIP acquisition shares the same canonical contract/profile.
        profile = event_sources.event_source_profile(endpoint, identity_map=identity,
                    next_open_session_by_date={"2000-01-01": "2000-01-03"})
        yield event_sources._DOMAINS[endpoint], endpoint, contract, profile
    yield "listing_events", "stock_basic", vendor_listing.CONTRACT, vendor_listing.PROFILE
    yield "universe_membership", "index_weight", vendor_membership.CONTRACT, vendor_membership.PROFILE


def _query(domain, contract, field):
    if domain == "universe_membership":
        return "Data.members / QuerySpec(fields=('is_member',), universe_id=...)" if field == "is_member" else "成员区间合同；查询使用 is_member 投影"
    if contract.get("logical_key") == ["security_id", "session"]:
        return "Data.read / QuerySpec；security_id、session 为隐含键"
    if "security_id" in contract.get("logical_key", []):
        return "Data.events / EventQuery；显式 time_field、cutoff 和 filters"
    return "Snapshot 合同/分区；Data.states、Data.plan_scope 读取其依赖"


def fact_dictionary(store, *, snapshot=None, domains=None, raw_batch_id=None):
    """Describe declared facts without a supplier call or a persisted artifact.

    No Snapshot: show code-supported adapters. A concrete Snapshot overlays its
    actual contracts/mappings; availability means declared, not non-null or PIT
    visible. By default only metadata is read. Optionally inspect exactly one
    explicitly selected Raw response to show unmapped columns; no Raw history
    scan is performed. Missing/corrupt requested objects keep the normal errors.
    """
    if snapshot in {"current", "latest"}:
        raise QueryError("resolve the snapshot before reading its dictionary")
    selected = None if domains is None else set(domains)
    if selected is not None and (not selected or any(not isinstance(d, str) or not d for d in selected)):
        raise QueryError("dictionary domains must be nonempty domain names")
    manifest = store.load_snapshot(snapshot) if snapshot is not None else None
    records = {}

    def add(domain, endpoint, contract, profile, *, present=False):
        if selected is not None and domain not in selected:
            return
        fields = dict(contract["fields"])
        if domain == "universe_membership":
            fields["is_member"] = {"dtype": "bool", "nullable": True}
        mapping = profile.get("field_map", {})
        availability = {k: v for k, v in profile.get("availability", {}).items()
                        if k != "next_open_session_by_date"}
        for name, spec in fields.items():
            raw_field = mapping.get(name)
            conversion = profile.get("unit_conversions", {}).get(name)
            record = dict(domain=domain, endpoint=endpoint, source_profile=profile.get("id"),
                contract_id=contract["contract_id"], canonical_field=name, raw_field=raw_field,
                meaning=spec.get("description", _MEANINGS.get(name, name)), dtype=spec.get("dtype"),
                nullable=spec.get("nullable", True), raw_unit=profile.get("source_units", {}).get(raw_field),
                unit=spec.get("unit"), conversion=deepcopy(conversion), basis=spec.get("basis"),
                logical_key=list(contract.get("logical_key", [])),
                time_role=_TIME_ROLES.get(name, "经济日期" if spec.get("dtype") == "date" else None),
                availability_rule=deepcopy(availability), revision_order=profile.get("revision_order"),
                query_method=_query(domain, contract, name), supported=True,
                snapshot_available=present if manifest is not None else None,
                status="Snapshot已声明" if present else "支持但该Snapshot未接入" if manifest is not None else "代码支持")
            records[(domain, endpoint, profile.get("id"), name)] = record

    for domain, endpoint, contract, profile in _catalogue():
        add(domain, endpoint, contract, profile)
    if manifest is not None:
        for domain, segment in manifest["domains"].items():
            profile = segment.get("source_profile", {})
            endpoint = profile.get("endpoint") or ("index_weight" if profile.get("id", "").startswith("tushare.index_weight") else None)
            add(domain, endpoint, segment["contract"], profile, present=True)
    if raw_batch_id is not None:
        from .sources import _response_table
        import json
        raw = store.get_raw(raw_batch_id)
        domain, profile = raw["domain"], raw.get("source_profile", {})
        if selected is not None and domain not in selected:
            raise QueryError("selected Raw lies outside dictionary domains")
        source_fields, _ = _response_table(json.loads(store.read_raw_record(raw)))
        connected = set(profile.get("field_map", {}).values())
        # These inputs feed the declared group/suspension normalizer, not
        # independent scalar Canonical fields.
        if profile.get("endpoint") == "top10_holders":
            connected.update(("holder_name", "hold_amount", "hold_ratio"))
        if profile.get("endpoint") == "suspend_d":
            connected.add("suspend_type")
        for name in sorted(set(source_fields) - connected):
            records[(domain, profile.get("endpoint"), profile.get("id"), "raw:" + name)] = dict(
                domain=domain, endpoint=profile.get("endpoint"), source_profile=profile.get("id"),
                canonical_field=None, raw_field=name, meaning="仅供应商 Raw 字段；尚无 Canonical 映射",
                unit=None, raw_unit=profile.get("source_units", {}).get(name), supported=False,
                snapshot_available=False if manifest is not None else None, status="仅Raw，未接入Canonical",
                query_method="无 Reader 字段；扩展合同/映射后从已存 Raw 重建")
    return {"schema_version": "fact_dictionary_v1", "snapshot_id": snapshot, "raw_batch_id": raw_batch_id,
            "fields": sorted(records.values(), key=lambda r: (r["domain"], r["endpoint"] or "", r["source_profile"] or "", r["canonical_field"] or r["raw_field"])),
            "notes": ["已声明不保证所选证券/日期有值；PIT 可见性和缺失原因以查询结果为准。",
                      "默认只读合同/来源映射；仅显式指定一个 Raw 才列出该响应未接入字段。",
                      "本字典管事实，Research 管 Feature 配方；未枚举 Tushare 全部可提供字段。"]}
