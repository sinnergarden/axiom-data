import unittest
from axiom_data.source_client import PacedSourceClient


class SourceClientTest(unittest.TestCase):
    def test_only_transient_rate_errors_retry(self):
        class Client:
            calls=0
            def query(self,*args,**kwargs):
                self.calls+=1
                if self.calls==1:raise Exception('每分钟访问次数限制')
                return [{'value':1}]
        slept=[];client=Client()
        wrapper=PacedSourceClient(client,sleep=slept.append,clock=lambda:0)
        self.assertEqual(wrapper.query('daily'),[{'value':1}]);self.assertEqual(client.calls,2)
        self.assertIn(61,slept)
        class Invalid:
            calls=0
            def query(self,*args,**kwargs):self.calls+=1;raise ValueError('source schema changed')
        invalid=Invalid()
        with self.assertRaises(ValueError):PacedSourceClient(invalid,sleep=slept.append).query('daily')
        self.assertEqual(invalid.calls,1)
        class Transport:
            calls=0
            def query(self,*args,**kwargs):
                self.calls+=1
                if self.calls<3:raise ConnectionError('temporary disconnect')
                return []
        transport=Transport();slept=[]
        self.assertEqual(PacedSourceClient(transport,sleep=slept.append,clock=lambda:0).query('daily'),[])
        self.assertEqual(transport.calls,3);self.assertIn(5,slept);self.assertIn(10,slept)
