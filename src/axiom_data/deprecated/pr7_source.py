"""Explicit historical Python aliases; current implementation is shared."""
from axiom_data.event_source import *
from axiom_data.event_source import EventBuilder as Pr7Builder
from axiom_data.event_source import EventCollector as Pr7Collector
from axiom_data.event_source import select_event_revisions as select_pr7_revisions

def load_pr7_source_profile(version="tushare_pr7.v1"):
    return load_event_source_profile(version)
