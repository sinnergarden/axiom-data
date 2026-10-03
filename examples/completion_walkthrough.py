"""Offline, synthetic end-to-end examples for the local Axiom Data demo.

Run from axiom-data with PYTHONPATH=src:../axiom-engine/src:../axiom-research/src:
../axiom-ui/src and the local dependencies installed. `--build-notebook` executes
the teaching cells and regenerates notebooks/synthetic_semantics.ipynb and HTML.
No supplier call, network access, or durable demonstration root is involved.
"""
from __future__ import annotations

import argparse
import contextlib
from copy import deepcopy
from datetime import datetime, timezone
import io
import json
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import uuid

from axiom_data import (Data, EventQuery, IngestBatch, QuerySpec, UpdateRequest,
                        adjust_prices,
                        export_bundle, import_bundle, single_quarter, ttm,
                        verify_bundle)
from axiom_engine.runtime import DecisionBatchGate, InputGateError, stale_price_mark
from axiom_research.data_adapter import adapt_decision_batch
from axiom_ui import chart_context, project_data_layer, unavailable_layer


REPO = Path(__file__).resolve().parents[1]
REF = "sha256:" + "a" * 64  # deterministic synthetic recipe reference
SESSIONS = ("2024-01-02", "2024-01-03", "2024-01-04")
CUTOFF = "2024-01-20T00:00:00+00:00"


def finance_example(temp: Path) -> dict:
    """Use Data.update/events and pure stable transforms, including retraction."""
    data = Data(temp / "finance")
    contract = {"contract_id": "demo.financial.v1",
                "logical_key": ["security_id", "endpoint", "report_type", "report_period"],
                "fields": {"security_id": {"dtype": "string", "nullable": False},
                           "endpoint": {"dtype": "string", "nullable": False},
                           "report_type": {"dtype": "string", "nullable": False},
                           "report_period": {"dtype": "date", "nullable": False},
                           "vendor_ann_date": {"dtype": "date"},
                           "revenue": {"dtype": "int64", "unit": "CNY",
                                       "basis": "cumulative_ytd", "status_field": "revenue__status"},
                           "revenue__status": {"dtype": "string"}}}
    profile = {"id": "demo.financial.source.v1", "data_nature": "synthetic",
               "field_map": {**{name: name for name in contract["fields"]},
                             "revision_id": "revision_id", "revision_sequence": "revision_sequence"},
               "source_units": {"revenue": "CNY"},
               "availability": {"date_field": "vendor_ann_date", "date_rule": "same_day_release",
                                "timezone": "Asia/Shanghai", "session_release_time": "17:00:00"},
               "revision_order": "source_sequence_only"}
    periods = ("2024-03-31", "2024-06-30", "2024-09-30", "2024-12-31", "2025-03-31")
    def row(period, amount, revision, sequence, status="value"):
        return {"security_id": "synthetic-A", "endpoint": "income",
                "report_type": "consolidated", "report_period": period,
                "vendor_ann_date": "2025-04-30", "revenue": amount,
                "revenue__status": status, "revision_id": revision,
                "revision_sequence": sequence}
    def ingest(rows, observed):
        return IngestBatch("financial_events", json.dumps(rows).encode(),
                           {"fixture": "synthetic financial history"}, contract, profile, observed)
    old = data.update(base_snapshot=None, request=UpdateRequest(
        (ingest([row(p, v, f"r{i}", 1) for i, (p, v) in
                 enumerate(zip(periods, (100, 200, 300, 400, 120)))],
                "2025-05-02T00:00:00Z"),), "demo-finance-v1", {"data_nature": "synthetic"}))
    query = EventQuery("financial_events", ("revenue",), ("synthetic-A",),
                       "2024-01-01", "2025-03-31", "2025-05-03T00:00:00Z",
                       "operational_pit_v1", "report_period",
                       filters={"endpoint": "income", "report_type": "consolidated"})
    source = data.events(snapshot=old.snapshot_id, query=query)
    args = dict(field="revenue", endpoint="income", report_type="consolidated",
                basis="cumulative_ytd", unit="CNY")
    quarter = single_quarter(source, **args)
    trailing = ttm(source, **args)
    assert quarter.frame["revenue"].tolist() == [100, 100, 100, 100, 120]
    assert trailing.frame["revenue"].iloc[-1] == 420
    new = data.update(base_snapshot=old.snapshot_id, request=UpdateRequest(
        (ingest([row("2024-09-30", None, "r2-retracted", 2, "retracted")],
                "2025-05-04T00:00:00Z"),), "demo-finance-retraction", {"data_nature": "synthetic"}))
    revised = data.events(snapshot=new.snapshot_id, query=EventQuery(
        "financial_events", ("revenue",), ("synthetic-A",), "2024-01-01", "2025-03-31",
        "2025-05-05T00:00:00Z", "operational_pit_v1", "report_period",
        filters={"endpoint": "income", "report_type": "consolidated"}))
    invalid = ttm(revised, **args)
    assert invalid.frame["revenue"].iloc[-1] is None or invalid.frame["revenue"].isna().iloc[-1]
    print("财务事件原生键:", list(source.frame.columns[:4]))
    print("单季收入:", quarter.frame["revenue"].tolist(), "CNY")
    print("旧 Snapshot 最近 TTM:", trailing.frame["revenue"].iloc[-1], "CNY")
    print("撤销后最近 TTM:", invalid.frame["revenue"].iloc[-1],
          "原因:", invalid.field_meta["revenue"]["by_key"][-1]["missing_reason"])
    return {"old_snapshot": old.snapshot_id, "new_snapshot": new.snapshot_id,
            "old_ttm": int(trailing.frame["revenue"].iloc[-1]),
            "new_missing_reason": invalid.field_meta["revenue"]["by_key"][-1]["missing_reason"]}


def adjustment_example(temp: Path) -> dict:
    """Retain the original common-anchor split example using the pure recipe."""
    data = Data(temp / "split")
    dates = ("2020-01-02", "2020-01-03")
    base = {"security_id": {"dtype": "string", "nullable": False},
            "session": {"dtype": "date", "nullable": False}}
    def input_batch(domain, field, values, unit):
        contract = {"contract_id": "synthetic." + domain + ".v1",
                    "logical_key": ["security_id", "session"],
                    "fields": {**base, field: {"dtype": "float64", "unit": unit}}}
        profile = {"id": "synthetic." + domain + ".source.v1",
                   "data_nature": "synthetic", "field_map": {name: name for name in contract["fields"]},
                   "source_units": {field: unit},
                   "availability": {"timezone": "Asia/Shanghai",
                                    "session_release_time": "20:00:00"}}
        rows = [{"security_id": "synthetic-split", "session": day, field: value}
                for day, value in zip(dates, values)]
        return IngestBatch(domain, json.dumps(rows).encode(),
                           {"fixture": "synthetic two-day split"}, contract, profile,
                           "2026-09-28T10:00:00+08:00")
    published = data.update(base_snapshot=None, request=UpdateRequest((
        input_batch("market_daily", "close", (10.0, 5.0), "CNY/share"),
        input_batch("adjustment_factor", "adj_factor", (1.0, 2.0), "ratio")),
        "demo-common-anchor", {"data_nature": "synthetic"}))
    q = QuerySpec("market_daily", ("close",), ("synthetic-split",), dates,
                  "best_effort_vendor_v1", {day: "2020-01-03T20:00:00+08:00" for day in dates})
    prices = data.read(snapshot=published.snapshot_id, query=q)
    from dataclasses import replace
    factors = data.read(snapshot=published.snapshot_id,
                        query=replace(q, domain="adjustment_factor", fields=("adj_factor",)))
    adjusted = adjust_prices(prices, factors, fields=("close",), anchor_session=dates[-1])
    values = adjusted.frame["close"].tolist()
    assert values == [5.0, 5.0]
    print("同一锚点复权价:", values, "；跨日收益:", values[1] / values[0] - 1)
    return {"values": values, "snapshot_id": published.snapshot_id,
            "anchor": adjusted.context["query"]["adjustment_anchor"]}


def reference_example(temp: Path) -> tuple[Data, str, QuerySpec]:
    """Build a small Raw/Parquet/Snapshot reference fixture, then diagnose it."""
    data = Data(temp / "references")
    store = data.store
    observed = "2024-01-01T00:00:00Z"
    def contract(name, key, fields):
        return {"contract_id": "demo." + name + ".v1", "logical_key": key,
                "fields": {k: {"dtype": dtype, "nullable": True,
                                **({"unit": "CNY/share"} if k == "close" else
                                   {"unit": "shares"} if k == "volume" else {})}
                           for k, dtype in fields.items()}}
    cal = contract("calendar", ["exchange", "session"],
                   {"exchange": "string", "session": "date", "is_open": "bool"})
    master = contract("master", ["security_id", "listing_date"],
                      {"exchange": "string", "listing_date": "date", "delisting_date": "date"})
    status = contract("status", ["security_id", "session"], {"is_suspended": "bool"})
    market = contract("market", ["security_id", "session"],
                      {"close": "float64", "volume": "int64"})
    member = contract("member", ["membership_id"],
                      {"security_id": "string", "universe_id": "string",
                       "membership_id": "string", "effective_from": "date", "effective_to": "date"})
    def fact(**values):
        return {"revision_id": "r1", "revision_sequence": 1,
                "first_observed_at": observed, **values}
    def add(name, rows, spec, *, partition="history", coverage=None):
        payload = json.dumps(rows, ensure_ascii=False).encode()
        raw = store.write_raw(payload, request={"fixture": name},
                              source_profile={"id": "synthetic-" + name},
                              observed_at=observed, contract=spec, domain=name)
        part = store.write_partition(name, partition,
                                     [dict(row, raw_batch_id=raw["batch_id"]) for row in rows], spec)
        return {"contract": spec, "source_profile": {"id": "synthetic-" + name,
                "data_nature": "synthetic"}, "partitions": [part],
                "raw_batch_ids": [raw["batch_id"]], "coverage": coverage or {},
                "build_context": {"demo": "synthetic direct reference fixture"}}
    days = ("2024-01-01", *SESSIONS)
    calendar_rows = [fact(exchange="DEMO", session=day, is_open=True) for day in days]
    identity_rows = [fact(security_id=symbol, exchange="DEMO", listing_date="2024-01-01",
                          delisting_date=None) for symbol in ("synthetic-A", "synthetic-B", "held-C")]
    status_rows = [fact(security_id="synthetic-A", session="2024-01-02", is_suspended=False),
                   fact(security_id="synthetic-A", session="2024-01-03", is_suspended=True),
                   fact(security_id="synthetic-A", session="2024-01-04", is_suspended=False)]
    market_rows = [fact(security_id="synthetic-A", session="2024-01-02", close=10.0,
                        volume=100, first_observed_at="2024-01-02T10:00:00Z"),
                   fact(security_id="synthetic-A", session="2024-01-03", close=10.0,
                        volume=0, first_observed_at="2024-01-03T10:00:00Z")]
    memberships = [fact(security_id="synthetic-A", universe_id="U", membership_id="a-1",
                        effective_from="2024-01-01", effective_to="2024-01-03"),
                   fact(security_id="synthetic-B", universe_id="U", membership_id="b-1",
                        effective_from="2024-01-03", effective_to=None),
                   fact(security_id="synthetic-A", universe_id="U", membership_id="a-2",
                        effective_from="2024-01-04", effective_to=None)]
    complete_states = [{"universe_id": "U", "complete": True,
                        "effective_from": day,
                        "effective_to": f"2024-01-{int(day[-2:])+1:02d}",
                        "members": names, "first_observed_at": observed,
                        "raw_batch_id": "synthetic-state-" + day}
                       for day, names in zip(days,
                           (["synthetic-A"], ["synthetic-A"],
                            ["synthetic-B"], ["synthetic-A", "synthetic-B"]))]
    domains = {"trading_calendar": add("trading_calendar", calendar_rows, cal),
               "security_master": add("security_master", identity_rows, master),
               "security_status": add("security_status", status_rows, status,
                                      partition="2024-01"),
               "market_daily": add("market_daily", market_rows, market,
                                   partition="2024-01", coverage={"complete_cells": [
                                       {"security_id": "synthetic-A", "session": "2024-01-04", "complete": True}]}),
               "universe_membership": add("universe_membership", memberships, member,
                                          coverage={"complete_states": complete_states})}
    snapshot = store.publish_snapshot(domains, parent_snapshot=None,
                                      build_context={"data_nature": "synthetic"})["snapshot_id"]
    query = QuerySpec("market_daily", ("close", "volume"), ("synthetic-A",), SESSIONS,
                      "operational_pit_v1", {s: CUTOFF for s in SESSIONS})
    states = data.states(snapshot=snapshot, query=query)
    observed_states = states.frame["market_state"].tolist()
    assert observed_states == ["normal_trading", "suspended", "source_gap"], observed_states
    scope = data.plan_scope(snapshot=snapshot, universe_id="U", output_sessions=SESSIONS,
                            cutoff_by_session={s: CUTOFF for s in SESSIONS},
                            pit_policy="operational_pit_v1", lookback_sessions=1,
                            held_symbols=("held-C",))
    assert scope["warmup_sessions"] == ["2024-01-01"]
    assert scope["read_symbols"] == ["held-C", "synthetic-A", "synthetic-B"]
    print("状态诊断:", observed_states, "；缺失价格不是零价")
    print("研究 scope: warmup=", scope["warmup_sessions"],
          "history union=", scope["historical_union_symbols"],
          "held=", scope["held_symbols"])
    print("研究 scope 缺口:", scope["missing_reasons"])
    return data, snapshot, query


def consumer_example(data: Data, snapshot: str, query: QuerySpec) -> dict:
    """Use the actual Research/Core ABI, Runtime gate and read-only UI layer."""
    from dataclasses import replace
    symbols = ("synthetic-A", "synthetic-B", "held-C")
    facts_query = replace(query, fields=("close",), symbols=symbols)
    members_query = QuerySpec("universe_membership", ("is_member",), symbols, SESSIONS,
                              "operational_pit_v1", {s: CUTOFF for s in SESSIONS},
                              universe_id="U")
    gate = DecisionBatchGate(data)
    gate.pin_session(SESSIONS[-1], snapshot=snapshot)
    try:
        gate.read_decision(decision_session=SESSIONS[-1],
                           query=replace(facts_query, purpose="label_outcomes"), clock=CUTOFF)
    except InputGateError:
        print("Runtime: label_outcomes 被决策输入闸门拒绝")
    else:
        raise AssertionError("label outcome crossed the decision gate")
    facts = gate.read_decision(decision_session=SESSIONS[-1], query=facts_query, clock=CUTOFF)
    reference = gate.read_decision(decision_session=SESSIONS[-1], query=members_query, clock=CUTOFF)
    adapted = adapt_decision_batch(facts, reference=reference, recipe_ref=REF)
    frame = adapted.execute()
    a = [row for row in frame.to_dict()["rows"] if row["security_id"] == "synthetic-A"]
    assert [row["values"] for row in a] == [[10.0, None, None], [10.0, 10.0, 0.0],
                                             [None, 10.0, None]]
    replay = gate.read_market(decision_session=SESSIONS[-1],
                              query=replace(facts_query, purpose="market_replay"), clock=CUTOFF)
    assert replay.context["query"]["purpose"] == "market_replay"
    view = adapted.view_ref.to_dict()
    chart = chart_context(mode="run_replay", security_id="synthetic-A",
        session_refs={s: view for s in SESSIONS}, price_basis="unadjusted",
        adjustment_anchor=None, time_axis="decision_available",
        generated_at="2026-09-28T00:00:00Z")
    layer = project_data_layer(facts, field="close", security_id="synthetic-A",
                               role="candles", chart=chart,
                               generated_at="2026-09-28T00:00:00Z")
    unavailable = unavailable_layer(role="feature", requested_ref="missing-feature-build",
                                    requested_stage="model_input", reason="ARTIFACT_NOT_FOUND",
                                    generated_at="2026-09-28T00:00:00Z")
    stale = stale_price_mark(10.0, price_session="2024-01-03",
        valuation_session="2024-01-04", calendar_sessions=SESSIONS,
        source_ref=adapted.view_ref.digest)
    assert layer["points"][-1]["value"] is None
    assert len(gate.references) == 3  # two decision inputs, one execution replay
    print("Core A 列 [value, lag, return]:", [row["values"] for row in a])
    print("Core 输出单位:", [col["unit"] for col in frame.to_dict()["schema"]])
    print("Runtime 固定读引用:", [(r.purpose, r.snapshot_id) for r in gate.references])
    print("旧价估值标记:", stale)
    print("UI Data 层: stage=", layer["stage"], "unit=", layer["unit"],
          "source freshness=", layer["freshness"], "generated_at=", layer["generated_at"])
    print("UI 缺失 Feature 层:", unavailable["status"], unavailable["requested_stage"])
    return {"core": frame, "chart": chart, "layer": layer,
            "references": gate.references, "stale": stale}


def portable_example(temp: Path, data: Data, snapshot: str, query: QuerySpec) -> dict:
    """Export, verify, import and replay the same saved query without network."""
    bundle, restored_root = temp / "portable-bundle", temp / "restored"
    before = data.read(snapshot=snapshot, query=query).to_json()
    exported = export_bundle(data.store.root, bundle, snapshot_id=snapshot,
                             code_root=REPO)
    verified = verify_bundle(bundle)
    selected = import_bundle(bundle, restored_root)
    restored = Data(restored_root)
    assert selected == snapshot == verified["snapshot_id"] == exported["snapshot_id"]
    assert restored.read(snapshot=selected, query=query).to_json() == before
    print("Portable: SHA-256 closure verified; offline read matches; Snapshot=", selected)
    print("Portable captured code files:", len([key for key in exported["files"]
                                                if key.startswith("code/")]))
    return {"bundle_id": verified["bundle_id"], "snapshot_id": selected}


def run_all(root: Path) -> dict:
    """Execute each bounded example under a caller-owned temporary directory."""
    adjustment = adjustment_example(root)
    finance = finance_example(root)
    data, snapshot, query = reference_example(root)
    consumer = consumer_example(data, snapshot, query)
    portable = portable_example(root, data, snapshot, query)
    return {"adjustment": adjustment, "finance": finance, "snapshot": snapshot,
            "consumer": consumer, "portable": portable}


def build_notebook(render_python=None) -> int:
    """Execute source cells, write actual outputs, and render HTML with nbconvert."""
    cells = []
    def markdown(source):
        cells.append({"cell_type": "markdown", "id": uuid.uuid4().hex[:8],
                      "metadata": {}, "source": source.splitlines(True)})
    def code(source):
        cells.append({"cell_type": "code", "id": uuid.uuid4().hex[:8],
                      "metadata": {}, "source": source.splitlines(True),
                      "execution_count": None, "outputs": []})
    markdown("""# Axiom Data：从 Raw 到跨仓消费者的可运行教学路径

这份 Notebook 使用真正的 `axiom_data`、Research adapter、Engine/Core、Runtime 输入闸门和 UI 投影代码。**第 1–6 节使用 synthetic fixture，第 7 节单独读取真实调试记录**；两类证据分开展示，不证明供应商历史公开 vintage 或全市场覆盖。仓库设计仍以 `design/02_axiom_data.md` 和 `design/06_axiom_ui.md` 为准。

推荐在 `axiom-data` 目录执行，Python 3.11+；需安装 `requirements-local.lock`，并将 Data、Engine、Research、UI 的 `src` 加入 `PYTHONPATH`。示例仅写临时目录，最后清理，无网络和凭据调用。""")
    code("""from pathlib import Path
from tempfile import TemporaryDirectory
import runpy, sys
repo = Path.cwd()
if repo.name == 'notebooks': repo = repo.parent
assert (repo / 'src/axiom_data').is_dir(), '请从 axiom-data 或 notebooks 目录运行'
for package in ('src', '../axiom-engine/src', '../axiom-research/src', '../axiom-ui/src'):
    sys.path.insert(0, str((repo / package).resolve()))
example = runpy.run_path(str(repo / 'examples/local_walkthrough.py'))
completion = runpy.run_path(str(repo / 'examples/completion_walkthrough.py'))
temporary = TemporaryDirectory(prefix='axiom-notebook-')
root = Path(temporary.name)
print('临时根目录已创建；以下均为 synthetic 数据。')
""")
    markdown("""## 1. Raw → typed Parquet → Snapshot → Reader

先运行原来的最小路径。显式 `Data.update` 保存源响应 Raw，规范化为 Parquet，再原子发布不可变 Snapshot。对同一事实的重复观察保留 Raw，却不强制发布新 Snapshot；增加一个 session 会产生新版本。读取只按固定 Snapshot + QuerySpec 工作，不创建 View 文件。""")
    code("""small_data, small_snapshot, small_query, small_batch = example['run'](root / 'small')
small_manifest = small_data.store.load_snapshot(small_snapshot.snapshot_id)
print('Snapshot 域:', list(small_manifest['domains']))
print('本次查询字段来源:', small_batch.field_meta['close']['by_key'][0]['availability_basis'])
print('API 响应生成时间:', small_batch.to_response()['context']['generated_at'])
""")
    markdown("""## 2. PIT 与价格派生：不要把探索假设当作历史公开证据

原例的 2020 价格在 2026 年才被本系统观察。`best_effort_vendor_v1` 可按显式来源假设用于探索，`operational_pit_v1` 不会倒填 2020 决策。复权时先用相同 Snapshot/PIT 截止选出价格和因子，再在一个窗口固定共同锚点；本 Notebook 的跨仓计算使用未复权价格，Runtime 回放也只能读取未复权行情。""")
    code("""from dataclasses import replace
strict = small_data.read(snapshot=small_snapshot.snapshot_id,
                         query=replace(small_query, pit_policy='operational_pit_v1'))
print('2020 operational 缺失原因:',
      [m['missing_reason'] for m in strict.field_meta['close']['by_key']])
assert all(m['missing_reason'] == 'not_visible_at_cutoff'
           for m in strict.field_meta['close']['by_key'])
adjustment = completion['adjustment_example'](root)
assert adjustment['values'] == [5.0, 5.0]
""")
    markdown("""## 3. 财务原生事件 → 单季 → TTM

`Data.events` 先在 knowledge cutoff 选修订，再按报告期范围投影。纯函数 `single_quarter` 从累计 YTD 求相邻差，`ttm` 要求连续四季；撤销某季后返回 null 与组件来源，而不是补零。这里的 100/200/300/400/120 是人为构造的 CNY 数值。""")
    code("""finance = completion['finance_example'](root)
assert finance['old_ttm'] == 420 and finance['new_missing_reason'] == 'retracted'
""")
    markdown("""## 4. 状态诊断与研究读取范围

参考域明确给出交易日历、证券身份、停牌和指数成分区间。`Data.states` 是诊断网格：正常、停牌与已声明覆盖的缺价分开，绝不将缺价编造成零价。`Data.plan_scope` 计算先前开放交易日的 warmup、历史成分并集和池外持仓；它报告证据缺口，不代替研究者判断可用性。""")
    code("""data, snapshot, query = completion['reference_example'](root)
print('固定 Snapshot:', snapshot)
""")
    markdown("""## 5. 消费边界：Data → Research → Core；Runtime 与 UI 分开

Research adapter 将同一个 DataBatch 的值、单位、逐键来源、缺失与每个 session 的 cutoff 映射至 Core ABI，执行的 value/lag/return 由 **Core** 算出。Runtime 在决策 session 固定 Snapshot，分别记录 decision_facts 与 market_replay 的真实查询引用；label_outcomes 不能进入决策事实。陈旧价格只作估值标记，不写回行情。UI 将保存的 Data query refs 投影为 Data 层；Feature 产物未保存时明确显示 `ARTIFACT_NOT_FOUND`，不在浏览时重算。`generated_at` 是查询生成时间，和源观察时间不同。""")
    code("""consumer = completion['consumer_example'](data, snapshot, query)
assert consumer['chart']['session_refs']['2024-01-02']['snapshot_id'] == snapshot
""")
    markdown("""## 6. 可移植恢复：固定闭包与离线重读

`export_bundle` 复制所选 Snapshot 的父链、Raw/Parquet 引用和当前工作树中的 Data 代码与依赖锁；`verify_bundle` 检查字节及闭包，`import_bundle` 在新根恢复。下面使用同一个 QuerySpec 验证恢复后结果一致。这个包是本地传输格式，不是来源真实性签名。""")
    code("""portable = completion['portable_example'](root, data, snapshot, query)
assert portable['snapshot_id'] == snapshot
""")
    markdown("""## 7. 真实三个月调试与后续接源

前面的教学输入是 synthetic；下面单独读取本地真实调试报告及固定 Snapshot。真实范围为
2026-06-01 至 2026-08-31，两只股票及沪深300指数；不是完整指数成分池。
数据取得之后，旧版本复读、同值日更、搬移和离线 Raw 重建分别验证。

新增供应商字段、迁移接口、新事件和供应商因子沿现有三层存储扩展，见
[接源演进说明](../docs/source-evolution.md)。本单元只读本机已有文件，不联网。""")
    code("""import json
from axiom_data import Data, QuerySpec
real_root = repo.parent / 'data/debug_three_months_202606_202608'
report_path = real_root / 'debug-report.json'
if report_path.is_file():
    report = json.loads(report_path.read_text())
    print('真实调试状态:', report['status'])
    print('域行数:', report.get('domains'))
    print('独立核对:', report.get('independent_checks'))
    if report['status'] == 'debug_chain_verified':
        sid = report['daily_refresh']['snapshot_id']
        days = ('2026-06-01', '2026-08-31')
        q = QuerySpec('market_daily', ('close', 'volume_shares'),
            ('debug-pingan', 'debug-pudong'), days, 'best_effort_vendor_v1',
            {day: day+'T23:00:00+08:00' for day in days})
        real = Data(real_root).read(snapshot=sid, query=q)
        print(real.frame.to_string(index=False))
        print('来源限制:', real.context['limitations'])
else:
    print('本机没有真实调试数据；上面的教学数据仍全部为 synthetic。')
reference_report = repo.parent / 'data/debug_reference_202606_202608/debug-report.json'
if reference_report.is_file():
    print('参考域独立调试:', json.dumps(json.loads(reference_report.read_text()), ensure_ascii=False, indent=2))
""")
    markdown("""## 8. 从小样本走向历史 bulk

长历史任务先固定证券身份、日期和来源范围，按交易日批量请求全市场后筛选固定范围，再按月及请求上限生成候选 Snapshot；全部块成功才切换 `current`。失败的块从 Raw 续跑，已完成的块不重新下载。这里用 **1800 个虚构代码**只计算请求规模，不下载数据，也不代表 CSI1800 的真实历史成员。

成员准备见 [reference readiness](../docs/reference-readiness.md)：主表包括上市、退市和暂停上市切片，月度成分观测只给出候选并集和有证据的日期。全历史拉取耗时主要由请求数、供应商限速和文件写入决定；下方时间是限速下限，不是完成承诺。

默认 CLI 为 `prepare → plan → run → status → audit`，读取、重建、导出和导入走同一套当前 API。旧 publication / materialized View 代码完整保存在 `../historical/axiom-data-precleanup-20260928/legacy_v1`，不进入安装包和默认测试；历史测试的原有失败记录保留，归档不等于修复。""")
    code("""from axiom_data import plan_bulk_job, estimate_bulk_job
synthetic_codes = tuple(f'{i:06d}.SZ' for i in range(1, 1801))
job = plan_bulk_job(mode='bulk', symbols=synthetic_codes,
    start_session='2014-11-01', end_session='2026-09-28',
    identity_map={code: 'synthetic-'+code for code in synthetic_codes},
    endpoints=('trade_cal', 'daily', 'adj_factor', 'suspend_d'))
estimate = estimate_bulk_job(job, min_interval_seconds=0)
print('仅规划，无供应商调用:', json.dumps(estimate, ensure_ascii=False, indent=2))
assert estimate['max_requests_in_chunk'] <= job.max_requests_per_chunk
""")
    markdown("""## 9. 一年真实验收与十二年运行

本地交付入口为 `axiom-data prepare → plan → run → verify → audit`。一年配置使用 2025-09-01 至 2026-08-31 的固定 1800 标的，验证工程链路；十二年配置采用月度历史候选并集，不能把固定锚点成员用于无幸存者偏差的历史回测。

请求按交易日批量发出，存储按月整理；中断只需再次执行同一 `run --plan`。完成任务可以不带 token 离线重跑。`rebuild` 从 Raw 离线重建，`export / verify / import` 搬移 Snapshot 及其闭包。详细命令和配置见 [CLI](../docs/cli.md)。下方只读取已保存的验收报告，不发出网络请求。""")
    code("""delivery_report = repo / 'docs/delivery-validation.json'
if delivery_report.is_file():
    print(json.dumps(json.loads(delivery_report.read_text()), ensure_ascii=False, indent=2))
else:
    print('一年验收报告尚未生成。')
""")
    markdown("""## 10. 支持边界与清理

本演示完成的是 Data 语义及 Data-facing 消费协议。它没有完整 UI 前端、订单/账户引擎、正式回测或 Qlib 消费者。真实小窗口只证明该次来源响应和所检验的链路；它不证明全市场覆盖或历史公开 vintage。教学与真实输出分开，不将供应商未知状态补成确定事实。""")
    code("""temporary.cleanup()
print('临时数据已清理；未调用供应商网络。')
""")
    namespace = {"__name__": "__main__"}
    count = 0
    for cell in cells:
        if cell["cell_type"] != "code":
            continue
        count += 1
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            exec(compile("".join(cell["source"]), f"notebook-cell-{count}", "exec"), namespace)
        cell["execution_count"] = count
        if output.getvalue():
            cell["outputs"] = [{"output_type": "stream", "name": "stdout",
                                "text": output.getvalue().splitlines(True)}]
    notebook = {"nbformat": 4, "nbformat_minor": 5,
                "metadata": {"kernelspec": {"display_name": "Python 3 (axiom-data)",
                                            "language": "python", "name": "python3"},
                             "language_info": {"name": "python", "version": "3.12"}},
                "cells": cells}
    target = REPO / "notebooks/synthetic_semantics.ipynb"
    target.write_text(json.dumps(notebook, ensure_ascii=False, indent=1) + "\n")
    subprocess.run([render_python or sys.executable, "-m", "nbconvert", "--to", "html", "--output",
                    "synthetic_semantics.html", target.name], cwd=target.parent, check=True)
    print(f"Executed {count} notebook cells; wrote {target} and HTML")
    return count


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build-notebook", action="store_true",
                        help="execute teaching cells and regenerate ipynb/html")
    parser.add_argument("--render-python", help="optional Python with nbconvert installed (default: current interpreter)")
    args = parser.parse_args()
    if args.build_notebook:
        build_notebook(args.render_python)
    else:
        with TemporaryDirectory(prefix="axiom-completion-") as directory:
            run_all(Path(directory))
