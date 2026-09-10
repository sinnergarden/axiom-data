"""Bounded transport retries for read-only queries; never retry a mapping error."""
import time


class PacedSourceClient:
    def __init__(self, client, *, interval=0.5, sleep=time.sleep, clock=time.monotonic):
        self.client=client;self.interval=interval;self.sleep=sleep;self.clock=clock;self.last={}

    def query(self, endpoint, **params):
        for attempt in range(3):
            delay=self.interval-(self.clock()-self.last.get(endpoint,float('-inf')))
            if delay>0:self.sleep(delay)
            self.last[endpoint]=self.clock()
            try:return self.client.query(endpoint,**params)
            except Exception as exc:
                message=str(exc).lower()
                limited=any(marker in message for marker in ('每分钟','访问频率','rate limit','too many requests'))
                transient=isinstance(exc,(ConnectionError,TimeoutError)) or type(exc).__name__ in {'ConnectionError','Timeout','ReadTimeout','ConnectTimeout'}
                if not (limited or transient) or attempt==2:raise
                self.sleep(61 if limited else 5*(attempt+1))
