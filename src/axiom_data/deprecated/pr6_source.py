"""Explicit historical Python aliases; current implementation is shared."""
from axiom_data.fundamentals_source import *
from axiom_data.fundamentals_source import FundamentalsBuilder as Pr6Builder
from axiom_data.fundamentals_source import FundamentalsCollector as Pr6Collector

def load_pr6_source_profile(version="tushare_pr6.v1"):
    return load_fundamentals_source_profile(version)
