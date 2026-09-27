"""Explicit historical Python aliases; current implementation is shared."""
from axiom_data.reference_source import *
from axiom_data.reference_source import TushareReferenceBuilder as TushareDm1Builder
from axiom_data.reference_source import TushareReferenceCollector as TushareDm1Collector

def load_dm1_source_profile(version="tushare_dm1.v1"):
    return load_reference_source_profile(version)

def dm1_source_profile_digest(profile=None):
    return reference_source_profile_digest(profile or load_dm1_source_profile())
