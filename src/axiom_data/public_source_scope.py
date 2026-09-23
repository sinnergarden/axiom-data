"""Scope checks shared by direct and operational security-session builds."""
from types import SimpleNamespace
from axiom_data.artifacts import ArtifactError
from axiom_data.consumption import validate_session, validate_symbols
from axiom_data.domains.market import _checked_security_identity_state
from axiom_data.consumption import exchange_sessions


def request_bounds(params):
    from axiom_data.fundamentals_source import source_date
    if params.get('trade_date'):
        day = source_date(params['trade_date'])
        return day, day
    if params.get('start_date') and params.get('end_date'):
        return source_date(params['start_date']), source_date(params['end_date'])
    raise ArtifactError('security-session source request requires explicit date bounds')


def request_symbols(params):
    value = params.get('ts_code')
    return validate_symbols(value.split(',')) if value else ('*',)


def validate_request_envelope(config, requests):
    """Prevent an operation from broadening the caller's source request scope."""
    if config.get('security_session_scope') != 'exchange_security.v1':
        return
    for symbol in validate_symbols(config['symbols']):
        bounds = [request_bounds(params) for params in requests
                  if '*' in request_symbols(params) or symbol in request_symbols(params)]
        if (not bounds or config['start_session'] < min(start for start, _ in bounds)
                or config['end_session'] > max(end for _, end in bounds)):
            raise ArtifactError('security session scope exceeds requested source scope')


def validate_security_scope(domain, config, raw_batches, dependencies):
    if 'security_session_scope' not in config:
        return
    from axiom_data.reference_source import _SECURITY_SESSION_SCOPE_DOMAINS
    if domain not in _SECURITY_SESSION_SCOPE_DOMAINS or config['security_session_scope'] != 'exchange_security.v1':
        raise ArtifactError('unsupported security session scope domain/policy')
    if not {'symbols','start_session','end_session'} <= set(config):
        raise ArtifactError('explicit security session scope required')
    symbols = validate_symbols(config['symbols'])
    start = validate_session(config['start_session'], 'start_session')
    end = validate_session(config['end_session'], 'end_session')
    if start > end:
        raise ArtifactError('reversed security session scope')
    reader = SimpleNamespace(security_master=lambda: dependencies['security_master'].rows,
                             trading_calendar=lambda: dependencies['trading_calendar'].rows)
    calendars = exchange_sessions(reader, symbols, start, end)
    identities = {row['symbol']: row for row in reader.security_master()}
    intervals = {}
    requests = []
    for raw in raw_batches:
        params = raw.manifest['request']['params']
        requests.append(params)
        bounds = request_bounds(params)
        for symbol in request_symbols(params):
            intervals.setdefault(symbol, set()).add(bounds)
    validate_request_envelope(config, requests)
    for symbol, calendar in calendars.items():
        windows = sorted(intervals.get(symbol, set()) | intervals.get('*', set()))
        position = 0
        for day in calendar['sessions']:
            state = _checked_security_identity_state(identities[symbol], day)
            if state == 'unknown':
                raise ArtifactError('unknown security identity interval')
            if state != 'within_identity_interval':
                continue
            while position < len(windows) and windows[position][1] < day:
                position += 1
            if position == len(windows) or windows[position][0] > day:
                raise ArtifactError('security session scope has a Raw request coverage gap')
