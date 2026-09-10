"""Versioned qualification of partial-session halts against actual traded bars."""
import json
import math
import re
from importlib.resources import files
from axiom_data.artifacts import ArtifactError,_digest,_json_bytes


def bind_profile(config):
    if 'session_suspension_policy' not in config:return
    if config['session_suspension_policy']!='session_suspension.v1':raise ArtifactError('unknown session suspension policy')
    profile=json.loads(files('axiom_data.source_profiles').joinpath('session_suspension.v1.json').read_bytes())
    config['session_suspension_profile_digest']=_digest(_json_bytes(profile))
    config['session_suspension_code']=_digest(files('axiom_data').joinpath('session_suspension.py').read_bytes())


def qualified_partial_halt(row, daily):
    timing=row.get('suspend_timing')
    if timing in (None,''):return False
    match=re.fullmatch(r'(\d{2}):(\d{2})-(\d{2}):(\d{2})',timing) if isinstance(timing,str) else None
    if not match:raise ArtifactError('unsupported suspension timing format')
    h1,m1,h2,m2=map(int,match.groups())
    start,end=h1*60+m1,h2*60+m2
    if m1>=60 or m2>=60 or not 570<=start<end<=900 or (start,end)==(570,900):
        raise ArtifactError('unsupported suspension timing interval')
    volume=daily.get('vol') if daily else None
    if (row.get('suspend_type') not in {'S','R'} or not daily or
        (daily.get('ts_code'),daily.get('trade_date'))!=(row.get('ts_code'),row.get('trade_date')) or
        isinstance(volume,bool) or not isinstance(volume,(int,float)) or not math.isfinite(volume) or not volume>0):
        raise ArtifactError('partial halt lacks same-session traded daily evidence')
    return True
