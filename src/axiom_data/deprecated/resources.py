"""Locate exact historical resources; their bytes and digests remain unchanged."""
from importlib.resources import files

PROFILE_NAMES = {
    "tushare_fina_indicator.v1": "tushare_fina_indicator.v2",
    "tushare_phase1.v1": "tushare_market.v1",
    "tushare_dm1.v1": "tushare_reference.v1",
    "tushare_pr6.v1": "tushare_fundamentals.v1",
    "tushare_pr6.v2": "tushare_fundamentals.v2",
    "tushare_pr7.v1": "tushare_events.v1",
    "tushare_pr7_holder.v2": "tushare_holder_reports.v2",
    "tushare_pr7_holder.v3": "tushare_holder_reports.v3",
}

def profile_generation(version):
    return PROFILE_NAMES.get(version, version)

def resource_file(family, name):
    current = files("axiom_data." + family).joinpath(name)
    return current if current.is_file() else files("axiom_data.deprecated." + family).joinpath(name)

def historical_profile(version):
    return version in PROFILE_NAMES

def source_reference(version, endpoint):
    generation = profile_generation(version)
    if version in {"tushare_pr6.v1", "tushare_pr6.v2", "tushare_fina_indicator.v1"}:
        # The indicator's published profile predates the current family naming.
        return "tushare.pr6." + endpoint
    if version in {"tushare_pr7.v1", "tushare_pr7_holder.v2", "tushare_pr7_holder.v3"}:
        return "tushare.pr7." + endpoint
    return ("tushare.events." if generation.startswith(("tushare_events", "tushare_holder_reports"))
            else "tushare.fundamentals.") + endpoint

def collector_revision(version, current):
    return {"tushare_dm1.v1":"tushare-dm1-collector.v1"}.get(version, current)

def builder_implementation(name):
    return {
        "axiom_data.dm1_source.TushareDm1Builder":"axiom_data.reference_source.TushareReferenceBuilder",
        "axiom_data.reference_source.TushareDm1Builder":"axiom_data.reference_source.TushareReferenceBuilder",
        "axiom_data.pr6_source.Pr6Builder":"axiom_data.fundamentals_source.FundamentalsBuilder",
        "axiom_data.pr7_source.Pr7Builder":"axiom_data.event_source.EventBuilder",
    }.get(name,name)

def builder_config(config):
    value = dict(config)
    if "dm1_source_partitioning" in value:
        value["reference_source_partitioning"] = value.pop("dm1_source_partitioning")
    return value


def collector_family(name):
    return {"dm1":"reference", "pr6":"fundamentals", "pr6_bulk":"fundamentals_bulk",
            "pr6_indicator":"financial_indicator", "pr7":"events",
            "pr7_holder":"holder_reports_v2", "pr7_holder_v3":"holder_reports_v3"}.get(name,name)


def raw_source_binding(manifest, endpoint, expected):
    """Resolve an older Raw binding only when it has the same source semantics."""
    version = manifest.get('source_profile_version')
    if not historical_profile(version) or profile_generation(version) != expected['source_profile_version']:
        return expected
    from axiom_data.artifacts import _digest, _json_bytes
    import json
    profile = json.loads(resource_file('source_profiles', version + '.json').read_bytes())
    if version == 'tushare_phase1.v1':
        from axiom_data.tushare import tushare_source_profile_digest
        digest = tushare_source_profile_digest(profile)
    elif version == 'tushare_dm1.v1':
        from axiom_data.reference_source import reference_source_profile_digest
        digest = reference_source_profile_digest(profile)
    else:
        digest = _digest(_json_bytes(profile))
    definition = profile['endpoints'][endpoint]
    return {'source_profile_ref': definition.get('source_profile_ref') or source_reference(version, endpoint),
            'source_profile_version': version, 'source_profile_digest': digest, 'fields': definition['fields']}


def reference_evidence_path(root, key):
    names = {'target':'reports/pr8/industry_authority/qualification.json',
             'industry':'reports/pr8/sw2021_canonical/anomaly_validation.json',
             'identity':'reports/pr8/sw2021_canonical/offline_validation.json'}
    return root / 'deprecated' / 'history' / names[key]


def collection_profile(family):
    return {'market':'tushare_phase1.v1', 'reference':'tushare_dm1.v1',
            'fundamentals':'tushare_pr6.v1', 'fundamentals_bulk':'tushare_pr6.v2',
            'financial_indicator':'tushare_fina_indicator.v1', 'events':'tushare_pr7.v1',
            'holder_reports_v2':'tushare_pr7_holder.v2',
            'holder_reports_v3':'tushare_pr7_holder.v3'}.get(collector_family(family))


def collection_binding(spec, current):
    version = collection_profile(spec['collector'])
    return raw_source_binding({'source_profile_version': version}, spec['endpoint'], current) if version else current


def collection_request_key(spec, current):
    from axiom_data.artifacts import _digest, _json_bytes
    return _digest(_json_bytes({'spec': spec, 'binding': collection_binding(spec, current)}))


def historical_collection_split(split):
    """Keep the original request selectors in an existing historical run graph."""
    import copy
    value = copy.deepcopy(split)
    names = {'reference':'dm1', 'fundamentals':'pr6', 'fundamentals_bulk':'pr6_bulk',
             'financial_indicator':'pr6_indicator', 'events':'pr7',
             'holder_reports_v2':'pr7_holder', 'holder_reports_v3':'pr7_holder_v3'}
    for spec in value['requests']:
        spec['collector'] = names.get(spec['collector'], spec['collector'])
    return value
