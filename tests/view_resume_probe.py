"""REPRODUCTION_EVIDENCE: public View resume on an isolated real fixture copy.

Run with PYTHONPATH=src:tests python3 tests/view_resume_probe.py.
Caller: targeted runner. No supplier access or formal data writes.
"""
import json
import time
from unittest.mock import patch

from axiom_data.artifacts import _json_bytes
from axiom_data.operations import _save
from axiom_data.views import _build_market_replay_view
from test_view_resume import ViewResumeTest


def main():
    fixture=ViewResumeTest()
    fixture.setUp()
    try:
        spec=fixture.args['views']['first']
        fixture.args['views']={f'view-{i}':spec for i in range(24)}
        results={}
        for stage in ('initial','resume'):
            writes=[]
            def save(path,value):
                writes.append((path,len(_json_bytes(value))))
                _save(path,value)
            started=time.perf_counter()
            with patch('axiom_data.view_operation.save_progress',side_effect=save), patch(
                    'axiom_data.views._build_market_replay_view',wraps=_build_market_replay_view) as builder:
                state=fixture.run_views()
            assert state['status']=='VIEWS_BUILT',state
            plan_bytes=len(_json_bytes(state['plan']))
            progress=[size for path,size in writes if path==fixture.path]
            results[stage]=dict(seconds=time.perf_counter()-started,builder_calls=builder.call_count,
                plan_writes=sum(path.name=='views-plan.json' for path,size in writes),
                progress_writes=len(progress),bytes_written=sum(size for path,size in writes),
                repeated_inline_plan_bytes_avoided=len(progress)*plan_bytes)
            if stage=='initial':published=state['published_views']
            else:
                assert state['published_views']==published
                assert builder.call_count==0
        print(json.dumps(dict(snapshot=fixture.args['snapshot_id'],symbol='688981.SH',
            sessions=['2025-06-10','2025-06-13'],labels=24,artifact_equal=True,
            results=results,measurement='serialized operation writes; excludes artifact writes and filesystem metadata; warm OS caches, isolated real fixture copy'),indent=2))
    finally:
        fixture.tearDown()


if __name__=='__main__':main()
