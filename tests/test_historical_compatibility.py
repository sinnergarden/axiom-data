"""Include the immutable evidence and protocol compatibility suite in normal discovery."""
from pathlib import Path
import sys
import unittest


def load_tests(loader, tests, pattern):
    historical = Path(__file__).resolve().parents[1] / 'deprecated' / 'tests'
    sys.path.insert(0, str(historical))
    return unittest.TestLoader().discover(str(historical), pattern or 'test*.py', top_level_dir=str(historical))
