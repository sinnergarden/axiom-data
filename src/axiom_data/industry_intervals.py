"""Frozen supplier industry intervals, explicitly terminal-history best effort."""
from collections import defaultdict
from .protocols import QueryError
from .reader import _revision_order


def read_industry_intervals(data, *, snapshot, security_ids):
    """Return native SW levels/dates and revision evidence without daily filling.

    This selects latest terminal revisions INSIDE the fixed Snapshot. It does
    not certify publication-time PIT, invent taxonomy vintage, or contact a
    supplier. Consumers must mark retrospective interval use as best effort.
    """
    if snapshot in ('current','latest') or not security_ids:
        raise QueryError('fixed snapshot and explicit securities required')
    metadata=data.describe_snapshot(snapshot=snapshot,domains=['industry_membership_events'],include_domain_refs=True)
    domain=data._reader(snapshot).snapshot['domains']['industry_membership_events']
    grouped=defaultdict(list)
    keys=domain['contract']['logical_key']
    for part in domain['partitions']:
        for row in data.store.read_partition(part,symbols=tuple(security_ids)).to_pylist():
            grouped[tuple(row[k] for k in keys)].append(row)
    rows=[_revision_order(values,str(key),{'revision_order':'terminal_observation_v1'}) for key,values in sorted(grouped.items())]
    data.describe_snapshot(snapshot=snapshot,domains=['industry_membership_events'])
    return {'snapshot_id':snapshot,'domain':metadata['domains']['industry_membership_events'],
            'qualification':'sw_supplier_intervals_best_effort_v1',
            'publication_pit':False,'records':rows}
