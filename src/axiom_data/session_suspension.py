"""Versioned qualification of partial-session halts against actual traded bars."""
import json
import math
import re
from importlib.resources import files
from axiom_data.artifacts import ArtifactError,_digest,_json_bytes


def bind_profile(config):
    if 'session_suspension_policy' not in config:return
    version=config['session_suspension_policy']
    if version not in {'session_suspension.v1','session_suspension.v2'}:raise ArtifactError('unknown session suspension policy')
    profile=json.loads(files('axiom_data.source_profiles').joinpath(version+'.json').read_bytes())
    config['session_suspension_profile_digest']=_digest(_json_bytes(profile))
    config['session_suspension_code']=_digest(files('axiom_data').joinpath('session_suspension.py').read_bytes())


def qualified_partial_halt(row, daily, policy='session_suspension.v1'):
    timing=row.get('suspend_timing')
    if timing in (None,''):
        if policy!='session_suspension.v2' or daily is None:return False
        # An untimed halt does not contradict an independently observed traded
        # bar. Do not invent its intraday boundaries; retain the source evidence.
    elif policy=='session_suspension.v2':
        _validate_traded_day_intervals(timing)
    elif policy=='session_suspension.v1':
        match=re.fullmatch(r'(\d{2}):(\d{2})-(\d{2}):(\d{2})',timing) if isinstance(timing,str) else None
        if not match:raise ArtifactError('unsupported suspension timing format')
        h1,m1,h2,m2=map(int,match.groups())
        start,end=h1*60+m1,h2*60+m2
        if m1>=60 or m2>=60 or not 570<=start<end<=900 or (start,end)==(570,900):
            raise ArtifactError('unsupported suspension timing interval')
    else:raise ArtifactError('unknown session suspension policy')
    volume=daily.get('vol') if daily else None
    if (row.get('suspend_type') not in {'S','R'} or not daily or
        (daily.get('ts_code'),daily.get('trade_date'))!=(row.get('ts_code'),row.get('trade_date')) or
        isinstance(volume,bool) or not isinstance(volume,(int,float)) or not math.isfinite(volume) or not volume>0):
        raise ArtifactError('partial halt lacks same-session traded daily evidence')
    return True


def _validate_traded_day_intervals(timing):
    intervals=[]
    for part in timing.split(',') if isinstance(timing,str) else []:
        match=re.fullmatch(r'(\d{1,2}):(\d{2})-(\d{1,2}):(\d{2})',part)
        if not match:raise ArtifactError('unsupported suspension timing format')
        h1,m1,h2,m2=map(int,match.groups())
        start,end=h1*60+m1,h2*60+m2
        if m1>=60 or m2>=60 or not 540<=start<end<=900:
            raise ArtifactError('unsupported suspension timing interval')
        if intervals and start<intervals[-1][1]:
            raise ArtifactError('unordered/overlapping suspension intervals')
        intervals.append((start,end))
    if not intervals:raise ArtifactError('unsupported suspension timing format')
    def covered(start,end):
        if start==end:return any(a<=start<=b for a,b in intervals)
        for a,b in intervals:
            if a<=start<=b:start=max(start,b)
            if start>=end:return True
        return False
    # 09:30 starts after the opening auction. A daily bar alone does not
    # identify when its trades occurred; retain that limitation in the profile.
    if covered(565,565) and covered(570,690) and covered(780,900):
        raise ArtifactError('halt covers all trading opportunities but daily trades exist')
