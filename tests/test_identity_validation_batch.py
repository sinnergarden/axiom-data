import unittest
from types import SimpleNamespace
from unittest.mock import patch
from axiom_data.artifacts import _validate_market_dependencies,ArtifactError
from axiom_data.domains import market
from test_artifacts import security_row

class IdentityValidationBatchTest(unittest.TestCase):
    def test_full_identity_checked_once_and_boundaries_unchanged(self):
        identity=security_row();identity.update(list_session='2025-01-01',delist_session='2025-02-01')
        security=SimpleNamespace(rows=[identity]);calendar=SimpleNamespace(rows=[
            dict(exchange='SZSE',session=s,is_open=True) for s in ('2024-12-31','2025-01-01','2025-01-31','2025-02-01')])
        with patch('axiom_data.artifacts.validate_security_master_rows',wraps=market.validate_security_master_rows) as check:
            _validate_market_dependencies([dict(symbol='000001.SZ',session='2025-01-01')]*100,calendar,security)
            self.assertEqual(check.call_count,1)
        for session in ('2024-12-31','2025-02-01'):
            with self.assertRaisesRegex(ArtifactError,'identity interval'):
                _validate_market_dependencies([dict(symbol='000001.SZ',session=session)],calendar,security)
        malformed=dict(identity,delist_session='2024-12-30')
        with self.assertRaises(market.MarketContractError):
            _validate_market_dependencies([],calendar,SimpleNamespace(rows=[malformed]))
