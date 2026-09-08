"""Scoped deny guards for reproducible Data-consumer and recovery tests."""
import builtins
import io
import os
import socket
from contextlib import ExitStack,contextmanager
from pathlib import Path
from unittest.mock import patch

@contextmanager
def deny_external_data(data_root):
    root=Path(data_root).resolve();attempts=[]
    def check(path):
        if isinstance(path,int):return
        resolved=Path(path).resolve();text=str(resolved).lower()
        if resolved.is_relative_to(root):return
        legacy=any(s in text for s in ('/sysq/','/qsys/','/.openclaw/','/var/lib/axiom-data'))
        data=resolved.suffix.lower() in {'.parquet','.feather','.bin'} or '/qlib' in text or '/sidecar' in text
        if legacy or data:
            attempts.append(str(resolved));raise PermissionError('LEGACY_DATA_READ_DENIED: '+str(resolved))
    def wrap(original):
        def checked(path,*args,**kwargs):check(path);return original(path,*args,**kwargs)
        return checked
    def no_network(*args,**kwargs):
        attempts.append('network');raise PermissionError('NETWORK_DISABLED')
    with ExitStack() as stack:
        for owner,name in [(builtins,'open'),(io,'open'),(os,'open')]:stack.enter_context(patch.object(owner,name,wrap(getattr(owner,name))))
        for owner,name in [(socket.socket,'connect'),(socket.socket,'connect_ex'),(socket,'create_connection'),(socket,'getaddrinfo')]:stack.enter_context(patch.object(owner,name,no_network))
        yield attempts
