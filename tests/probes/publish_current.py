"""Generate current public evidence pointers from one explicitly verified source commit."""
import argparse
from datetime import datetime
import hashlib
import json
from pathlib import Path
import subprocess
from zoneinfo import ZoneInfo

ROOT=Path(__file__).resolve().parents[2]


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def generate(record_path,verified_commit,branch):
    record_path=Path(record_path).resolve()
    if not record_path.is_relative_to(ROOT/'evidence'):raise ValueError('Record must be under the project evidence directory')
    relative=str(record_path.relative_to(ROOT))
    record=json.loads(record_path.read_text())
    commit=subprocess.check_output(['git','rev-parse',verified_commit+'^{commit}'],cwd=ROOT,text=True).strip()
    remote=subprocess.check_output(['git','ls-remote','origin','refs/heads/'+branch],cwd=ROOT,text=True).split()
    if not remote or remote[0]!=commit:raise ValueError('The named remote branch must point to the verified source commit')
    tracked=subprocess.check_output(['git','ls-files','-z','nativepeers','schemas','pyproject.toml','uv.lock'],cwd=ROOT).decode().split('\0')
    names=sorted(name for name in tracked if name and (name.endswith('.py') or name.startswith('schemas/') or name in ('pyproject.toml','uv.lock')))
    for name in names:
        committed=subprocess.check_output(['git','show',commit+':'+name],cwd=ROOT)
        if committed!=(ROOT/name).read_bytes():raise ValueError('Unpublished product source: '+name)
    source={name:digest(ROOT/name) for name in names}
    source_hash=hashlib.sha256(json.dumps(source,sort_keys=True,separators=(',',':')).encode()).hexdigest()
    at=datetime.now(ZoneInfo('America/New_York')).isoformat()
    record.update(code_commit=commit,source=source,source_map_sha256=source_hash,updated_at=at)
    cp=json.loads((ROOT/'evidence/checkpoint.json').read_text())
    fp=json.loads((ROOT/'evidence/fingerprints.json').read_text())
    if cp.get('current_review',{}).get('code_commit')!=commit:
        prior={key:cp.get(key) for key in ('stage','updated_at','git','publication','current_review')}
        history=cp.setdefault('publication_history',[])
        if prior not in history:history.append(prior)
    cp.update(stage=record['stage'],updated_at=at,tests_current=record['tests'],
        current_review=dict(branch=branch,code_commit=commit,evidence=relative,source_map_sha256=source_hash,status=record['status']))
    cp['git']=dict(base_commit=record['reviewed_commit'],branch=branch,code_commit=commit,last_verified_publication=commit,owned_uncommitted_changes=[])
    cp['publication']=dict(branch=branch,repository='https://github.com/scalinity/NativePeers',status='PUSHED',
        verified_remote_commit=commit,verified_remote_code_commit=commit,source_zip_uploaded=False,
        scope='Verified remote product source commit. Later evidence and documentation publication commits are descendants; native acceptance remains operation-specific.')
    cp['next_action']='Use operation-specific readiness and retained verification limits; independent review may inspect this complete source.'
    fp.update(at=at,code_commit=commit,current_verification=relative,source_sha256=source)
    for name in fp['canonical_and_dependency_sha256']:fp['canonical_and_dependency_sha256'][name]=digest(ROOT/name)
    for target,value in ((record_path,record),(ROOT/'evidence/checkpoint.json',cp),(ROOT/'evidence/fingerprints.json',fp)):
        target.write_text(json.dumps(value,indent=2,ensure_ascii=False)+'\n')
    return {'code_commit':commit,'evidence':relative,'source_entries':len(source),'source_map_sha256':source_hash}


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--record',type=Path,required=True)
    parser.add_argument('--verified-commit',required=True)
    parser.add_argument('--branch',required=True)
    args=parser.parse_args()
    print(json.dumps(generate(args.record,args.verified_commit,args.branch)))
