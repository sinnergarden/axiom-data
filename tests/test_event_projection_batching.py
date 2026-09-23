import json
import unittest
from pathlib import Path
from fixture_locations import fixture_root
from unittest.mock import patch

from axiom_data import SnapshotReader
from axiom_data.event_views import project, LEAF_DOMAINS
from axiom_data.pit import instant


class EventProjectionBatchingTest(unittest.TestCase):
    def test_batch_matches_every_direct_fact_and_metadata_at_multiple_cutoffs(self):
        run=json.loads(Path('reports/pr7/run_manifest.json').read_bytes())
        reader=SnapshotReader(fixture_root(run['source_root']),run['refs']['snapshot_id'])
        scope=dict(symbols=['600036.SH','688981.SH','000401.SZ'],start_session='2025-06-10',end_session='2025-06-13')
        for policy in ['best_effort_vendor_v1','operational_pit_v1']:
            for cutoff in ['2025-06-11T23:59:59+08:00','2025-06-13T23:59:59+08:00']:
                with patch.object(reader,'as_of',wraps=reader.as_of) as selected, \
                     patch.object(reader,'facts',wraps=reader.facts) as prepared:
                    result=project(reader,scope,policy,cutoff)
                    self.assertEqual(selected.call_count,8)  # Two daily domains; event selectors still run each session.
                    for domain in ('holder_count_events','top_holders_reports','forecast_observations'):
                        self.assertEqual(sum(c.args[0]==domain for c in prepared.call_args_list),1)
                self.assertEqual(len(result['wide']),12)
                for row in result['wide']:
                    effective=min(instant(cutoff),instant(row['session']+'T23:59:59+08:00')).isoformat()
                    for leaf in LEAF_DOMAINS:
                        expected=reader.leaf_fact(leaf,symbol=row['symbol'],target_session=row['session'],knowledge_cutoff=effective,pit_policy=policy)
                        self.assertEqual(row['facts'][leaf],expected)
