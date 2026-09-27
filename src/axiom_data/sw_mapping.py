"""The single explicitly authorized SW2021 source anomaly; never a name aliaser."""
from axiom_data.deprecated.resources import resource_file, profile_generation, historical_profile, source_reference
from copy import deepcopy
from importlib.resources import files
import json
from axiom_data import ArtifactError
from axiom_data.artifacts import _digest, _json_bytes


def load_mapping_profile():
    return json.loads(resource_file('source_profiles', 'tushare_sw2021.v1.json').read_bytes())


def canonical_taxonomy(taxonomy, members, *, profile=None, taxonomy_raw_refs=(), membership_raw_refs=()):
    profile=deepcopy(profile if profile is not None else load_mapping_profile())
    mappings=profile.get('anomaly_mappings',[])
    if (profile.get('profile_version')!='tushare_sw2021.v1' or
        profile.get('mapping_revision')!='sw2021-special-steel.v1' or
        len(mappings)!=1):
        raise ArtifactError('SW2021 requires the explicit versioned anomaly mapping')
    mapping=mappings[0]
    expected={'source_endpoint':'index_classify','source_code':'850401.SI','source_name':'特钢Ⅲ',
              'source_level':'L3','source_parent_code':'230500','source_industry_code':'230501','canonical_code':'850412.SI'}
    if any(mapping.get(k)!=v for k,v in expected.items()) or not mapping.get('evidence_refs'):
        raise ArtifactError('unsupported SW2021 anomaly mapping')
    source=[r for r in taxonomy if r.get('index_code')=='850401.SI']
    if not source or any((r.get('industry_name'),r.get('level'),r.get('parent_code'),r.get('industry_code'))!=
                         ('特钢Ⅲ','L3','230500','230501') for r in source):
        raise ArtifactError('SW2021 anomaly condition 1: taxonomy identity conflict')
    named=[r for r in members if r.get('l3_name')=='特钢Ⅲ']
    if not named or any(r.get('l3_code')!='850412.SI' for r in named):
        raise ArtifactError('SW2021 anomaly condition 2: member code conflict')
    if any(r.get(level+'_code')=='850401.SI' for r in members for level in ('l1','l2','l3')):
        raise ArtifactError('SW2021 anomaly condition 3: actual old-code membership')
    for row in members:
        for level in ('l1','l2','l3'):
            if row.get(level+'_code')=='850412.SI' and (level!='l3' or
                (row.get('l3_name'),row.get('l2_code'),row.get('l1_code'))!=('特钢Ⅲ','801045.SI','801040.SI')):
                raise ArtifactError('SW2021 anomaly condition 4: canonical node conflict')
    # A supplier-side fix is a new profile decision, not a silent change of v1.
    if any(r.get('index_code')=='850412.SI' for r in taxonomy):
        raise ArtifactError('SW2021 taxonomy changed; new mapping profile required')
    provenance={'mapping_revision':profile['mapping_revision'],'mapping_profile_digest':_digest(_json_bytes(profile)),
                'source_endpoint':'index_classify','source_code':'850401.SI','canonical_code':'850412.SI',
                'reason':mapping['reason'],'evidence_refs':mapping['evidence_refs'],
                'taxonomy_raw_refs':list(taxonomy_raw_refs),'membership_raw_refs':list(membership_raw_refs),
                'automatic_conditions':[True,True,True,True]}
    result=deepcopy(taxonomy)
    for row in result:
        row['source_index_code']=row['index_code']
        row['mapping_provenance']=deepcopy(provenance) if row['index_code']=='850401.SI' else None
        if row['index_code']=='850401.SI':row['index_code']='850412.SI'
    return result,provenance
