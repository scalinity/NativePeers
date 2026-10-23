"""Native status-line tee. Forward the original display and retain only quota fields."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

from ..errors import require
from ..journal import Journal
from ..launch import provider_home
from ..process import ancestry
from .normalize import PARSERS
from . import store


def entry_key(provider,home):
    return provider+':'+hashlib.sha256(str(Path(home).resolve()).encode()).hexdigest()


def route(provider):
    if provider=='antigravity':return 'native-google'
    for flag,name in [('CLAUDE_CODE_USE_BEDROCK','bedrock'),('CLAUDE_CODE_USE_VERTEX','vertex'),
                      ('CLAUDE_CODE_USE_FOUNDRY','foundry'),('ANTHROPIC_BASE_URL','configured-gateway'),
                      ('ANTHROPIC_API_KEY','api-key')]:
        if os.environ.get(flag):return name
    return 'native-auth-unknown'


def signature(path):
    p=Path(path).resolve(strict=True);s=p.stat()
    return dict(path=str(p),device=s.st_dev,inode=s.st_ino,size=s.st_size,mtime_ns=s.st_mtime_ns)


def capture(journal,provider,home,value,contract,*,owners=None):
    require(Path(home).resolve()==provider_home(provider),'CALLER_UNBOUND','Status-line provider home differs from native configuration')
    owners=owners if owners is not None else [p.json() for p in ancestry()[1:]]
    require(any(p['executable']==contract['runtime_stat']['path'] for p in owners),
        'CALLER_UNBOUND','Usage telemetry has no native owner')
    require(signature(contract['runtime_stat']['path'])==contract['runtime_stat'],
        'CAPABILITY_MISMATCH','Status-line runtime changed')
    native=value.get('session_id') if provider=='claude' else value.get('conversation_id') or value.get('session_id')
    import uuid
    try:uuid.UUID(native)
    except (ValueError,TypeError,AttributeError):require(False,'CALLER_UNBOUND','Status-line payload lacks a native UUID')
    pools=PARSERS[provider](value)
    if not pools:return None
    m=value.get('model') or {};model=m.get('id')
    models=[]
    if isinstance(model,str) and 0<len(model)<=512:
        models=[dict(model=model,display_name=str(m.get('display_name',model))[:512],
            capability_source='native-statusline',support='NATIVE_SELECTION',entitlement='UNVERIFIED')]
    catalog=store.latest(journal,provider,home,'catalog')
    known=(catalog['payload'].get('models',[]) if catalog else [])+models
    for p in pools:
        matched=[m['model'] for m in known if p['pool_id'] in (m['model'],m.get('display_name'))]
        if matched:p.update(models=matched,membership='exact-native-model-key')
    version=value.get('version')
    previous=store.latest(journal,provider,home,'snapshot')
    if previous:
        # Passive status lines may omit model-weekly/credit pools exposed by
        # an explicit native read. Preserve those rows with their own clocks.
        incoming={p['pool_id'] for p in pools}
        for old in previous['payload'].get('pools',[]):
            if old['pool_id'] in incoming:continue
            retained=dict(old)
            retained.setdefault('observed_at',previous['observed_at'])
            retained.setdefault('observation_source',previous['payload']['source'])
            retained.setdefault('observation_source_version',previous['payload'].get('source_version'))
            pools.append(retained)
        pools=pools[:512]
    return store.record(journal,provider,home,dict(source=provider+'-statusline',
        source_version=dict(runtime_sha256=contract['runtime_sha256'],native_version=str(version)[:64]),
        route=route(provider),pools=pools,models=known[:64],coverage='partial',
        limitations=['Status-line emission is not an independent account refresh; underlying provider measurement age may be unavailable.',
            'Additional unexposed model allowances remain unknown.']),dedupe=True)


def forward(stream,command):
    """Preserve all original input/output, but bound the copy eligible for parsing."""
    process=subprocess.Popen(command,shell=True,stdin=subprocess.PIPE) if command else None
    raw=bytearray();oversized=False
    try:
        while chunk:=stream.read(65536):
            if len(raw)+len(chunk)<=262144:raw.extend(chunk)
            else:oversized=True
            if process and process.stdin:
                try:process.stdin.write(chunk);process.stdin.flush()
                except BrokenPipeError:process.stdin.close();process.stdin=None
    finally:
        if process and process.stdin:process.stdin.close()
    code=process.wait() if process else 0
    return None if oversized else bytes(raw),code


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--provider',choices=['claude','antigravity'],required=True)
    parser.add_argument('--home',type=Path,required=True);parser.add_argument('--state',type=Path,required=True)
    args=parser.parse_args();contract=None
    try:contract=json.loads((args.state/'usage-install.json').read_text())['providers'][entry_key(args.provider,args.home)]
    except (OSError,ValueError,KeyError):pass
    command=((contract or {}).get('previous') or {}).get('command')
    raw,code=forward(sys.stdin.buffer,command)
    if raw and contract:
        journal=None
        try:
            journal=Journal.existing(args.state)
            capture(journal,args.provider,args.home,json.loads(raw),contract)
        except Exception:
            # Telemetry must never replace or break an existing status line.
            pass
        finally:
            if journal:journal.close()
    raise SystemExit(code)


if __name__=='__main__':main()
