"""Finite mappings for the previously published View identities."""

KINDS = {"pr6_fact": "financial_fact", "pr7_fact": "event_fact"}
SCHEMAS = {
    **{f"pr6_fact_view.v{n}": f"financial_fact_view.v{n}" for n in range(1, 7)},
    **{f"pr7_fact_view.v{n}": f"event_fact_view.v{n}" for n in range(1, 6)},
}
EXECUTION_SCHEMAS = {"pr6_fact_view.v6", "pr7_fact_view.v5"}

def historical_view_kind(identity):
    if isinstance(identity, str):
        if identity.startswith("pr6-fact-"): return "pr6_fact"
        if identity.startswith("pr7-fact-"): return "pr7_fact"
    return None

def current_view_kind(kind):
    return KINDS.get(kind, kind)

def view_schema(manifest):
    value = manifest.get("schema_version")
    return SCHEMAS.get(value, value)

def view_artifact_type(manifest):
    value = manifest.get("artifact_type")
    return {"pr6_fact_view":"financial_fact_view", "pr7_fact_view":"event_fact_view"}.get(value,value)

def view_storage_kind(identity):
    old = historical_view_kind(identity)
    if old: return old
    if identity.startswith("financial-fact-"): return "financial_fact"
    if identity.startswith("event-fact-"): return "event_fact"
    return "market_qlib"


def catalog_entries(layout, directories, view_reader):
    from axiom_data.artifacts import CatalogEntry
    from axiom_data.financial_views import load_financial_fact_view_with_reader
    from axiom_data.event_views import load_event_fact_view_with_reader
    entries = []
    for kind, loader in (("pr6_fact",load_financial_fact_view_with_reader), ("pr7_fact",load_event_fact_view_with_reader)):
        for path in directories(layout.root, layout.derived_commits(kind)):
            reader, schema = view_reader(path)
            view = loader(layout.root,path.name,checked_reader=None if schema == "pr6_fact_view.v1" else reader)
            for artifact_type in (view.manifest["artifact_type"], "qlib_view"):
                entries.append(CatalogEntry(artifact_type,view.ref.view_id,kind,schema,
                    (path/"manifest.json").relative_to(layout.root).as_posix(),view.ref.manifest_digest))
    return entries


def equivalent_invocation(stored, requested):
    """Compare only renamed entrypoints/kinds, retaining all original run inputs."""
    import copy
    functions = {
        "axiom_data.pr6_views.build_pr6_fact_view":"axiom_data.financial_views.build_financial_fact_view",
        "axiom_data.pr7_views.build_pr7_fact_view":"axiom_data.event_views.build_event_fact_view",
    }
    def normalized(value):
        value = copy.deepcopy(value)
        if 'function' in value:
            value['function'] = functions.get(value['function'], value['function'])
        if 'builder' in value:
            from axiom_data.deprecated.resources import builder_implementation
            value['builder'] = builder_implementation(value['builder'])
        kwargs = value.get('kwargs', {})
        for spec in kwargs.get('views', {}).values():
            if isinstance(spec, dict) and isinstance(spec.get('kind'), str):
                spec['kind'] = current_view_kind(spec['kind'])
        return value
    return normalized(stored) == normalized(requested)


def financial_checked_reader(root, identity, reader):
    if historical_view_kind(identity) != 'pr6_fact':
        return reader
    from axiom_data.artifacts import _layout, _load_manifest
    layout = _layout(root)
    manifest, _ = _load_manifest(layout.root, layout.derived_commits('pr6_fact') / identity,
        artifact_type='pr6_fact_view', schema_version=tuple('pr6_fact_view.v'+str(n) for n in range(1,7)),
        identity_field='view_id', identity=identity)
    return None if manifest['schema_version'] == 'pr6_fact_view.v1' else reader


def equivalent_recovery_plan(stored, requested):
    import copy
    def normalized(plan):
        plan = copy.deepcopy(plan)
        for field in ('views','rebuild_views'):
            for spec in (plan.get(field) or {}).values():
                spec['kind'] = current_view_kind(spec['kind'])
        return plan
    return normalized(stored) == normalized(requested)
