"""Read-only remote snapshot proof and private plan; no remote write APIs.

An observed head supplied at final verification is not an atomic lease. A later
separately authorized executor must enforce the emitted compare-and-swap guard.
"""
from __future__ import annotations
import hashlib,json,os,re,shutil,tempfile
from datetime import datetime,timezone
from pathlib import Path
from zoneinfo import ZoneInfo
from . import gaza_correction_activation as authority
from . import gaza_corrected_manifest_reader as reader
from . import gaza_correction_proofs as proof
from . import gaza_historical_correction as legacy
from . import gaza_oct25_release_package as private
Error=authority.Error
SCHEMA='gaza_oct25_verified_remote_snapshot_plan_v1'
REPOSITORY='RedGarland/the-blue-fern-co-dispatches'
FLAGS={'publication_authorized':False,'activation_authorized':False,'audio_authorized':False,'social_authorized':False,'rollback_execution_authorized':False}

def _now():return datetime.now(timezone.utc)
def _sha(raw):return hashlib.sha256(raw).hexdigest()
def _oid(raw,kind):return hashlib.sha1(kind.encode()+b' '+str(len(raw)).encode()+b'\0'+raw).hexdigest()
def _json(value):return legacy._json_document(value)
def _same(a,b):return legacy._canonical_bytes(a)==legacy._canonical_bytes(b)
def _read(root,path):return reader._read_regular(root,path)

def _raw_commit(raw,expected):
    authority._oid(expected)
    if _oid(raw,'commit')!=expected:raise Error('raw commit object does not hash to approved Pages head')
    if b'\n\n' not in raw:raise Error('raw commit header is incomplete')
    header=raw.split(b'\n\n',1)[0].split(b'\n')
    trees=[line[5:] for line in header if line.startswith(b'tree ')]
    if len(trees)!=1 or not re.fullmatch(b'[0-9a-f]{40}',trees[0]):raise Error('raw commit has invalid tree identity')
    return trees[0].decode()

def _tree_bytes(children):
    raw=b''
    for name,row in sorted(children,key=lambda pair:(pair[0]+('/' if pair[1]['type']=='tree' else '')).encode('utf-8')):
        raw+=row['mode'].lstrip('0').encode()+b' '+name.encode('utf-8')+b'\0'+bytes.fromhex(row['sha'])
    return raw

def validate_tree(document,expected):
    if document.get('sha')!=expected or document.get('truncated') is not False or not isinstance(document.get('tree'),list):
        raise Error('complete nontruncated approved recursive tree required')
    entries={};children={}
    for row in document['tree']:
        if not isinstance(row,dict):raise Error('invalid tree row')
        path=row.get('path');authority._path(path);authority._oid(row.get('sha'))
        if '\x00' in path or path in entries:raise Error('duplicate or unsafe tree path')
        if (row.get('mode'),row.get('type')) not in {('040000','tree'),('100644','blob'),('100755','blob'),('120000','blob'),('160000','commit')}:
            raise Error('unsupported tree mode/type')
        entry={key:row[key] for key in ('mode','type','sha')};entries[path]=entry
        children.setdefault(Path(path).parent.as_posix(),[]).append((Path(path).name,entry))
    if '.' not in children:raise Error('recursive tree root missing')
    for parent,rows in children.items():
        if parent!='.' and (parent not in entries or entries[parent]['type']!='tree'):raise Error('recursive tree parent missing')
        if _oid(_tree_bytes(rows),'tree')!=(expected if parent=='.' else entries[parent]['sha']):raise Error('recursive tree reconstruction differs')
    for path,row in entries.items():
        if row['type']=='tree' and path not in children and row['sha']!=_oid(b'','tree'):raise Error('recursive subtree was omitted')
    return entries

def _absence(entries,path):
    if any(p==path or p.startswith(path+'/') for p in entries):raise Error('required absent path exists in pinned tree')
    for parent in Path(path).parents:
        if parent==Path('.'):continue
        if parent.as_posix() in entries and entries[parent.as_posix()]['type']!='tree':raise Error('absent destination has non-tree ancestor')

def _snapshot_blob(root,entries,path):
    row=entries.get(path)
    if row is None or row['mode']!='100644' or row['type']!='blob':raise Error('required snapshot preimage is not a regular nonexecutable blob')
    raw=_read(root,'preimages/'+path)
    if _oid(raw,'blob')!=row['sha']:raise Error('snapshot preimage does not match pinned Git blob')
    return raw

def _result_tree(entries,outputs):
    updated={p:dict(row) for p,row in entries.items()}
    for path,raw in outputs.items():
        updated[path]={'mode':'100644','type':'blob','sha':_oid(raw,'blob')}
        for parent in Path(path).parents:
            if parent==Path('.'):continue
            name=parent.as_posix()
            if name in updated and updated[name]['type']!='tree':raise Error('write destination ancestor is not a tree')
            updated.setdefault(name,{'mode':'040000','type':'tree','sha':_oid(b'','tree')})
    parents={'.'}|{p for p,r in updated.items() if r['type']=='tree'};objects={}
    for parent in sorted(parents,key=lambda p:(p.count('/')+1 if p!='.' else 0),reverse=True):
        children=[(Path(p).name,row) for p,row in updated.items() if Path(p).parent.as_posix()==parent]
        raw=_tree_bytes(children);oid=_oid(raw,'tree');objects[oid]=raw
        if parent=='.':root=oid
        else:updated[parent]['sha']=oid
    before={p:r for p,r in entries.items() if r['type']!='tree' and p not in outputs}
    after={p:r for p,r in updated.items() if r['type']!='tree' and p not in outputs}
    if before!=after:raise Error('nonallowlisted leaf or mode changed')
    actual_changed={p for p in entries.keys()|updated.keys() if (entries.get(p) or {}).get('type')!='tree' and (updated.get(p) or {}).get('type')!='tree' and entries.get(p)!=updated.get(p)}
    if actual_changed!=set(outputs):raise Error('resulting tree differs from exact eleven-path write set')
    return root,updated,objects

def _verified_snapshot(snapshot,expected_head):
    commit_raw=_read(snapshot,'commit.raw');tree_oid=_raw_commit(commit_raw,expected_head)
    tree_raw=_read(snapshot,'tree.json');entries=validate_tree(authority._json(tree_raw),tree_oid)
    originals={};evidence={'evidence/snapshot/commit.raw':commit_raw,'evidence/snapshot/tree.json':tree_raw}
    for path in authority.PUBLIC_HASHES:
        if path.startswith('gaza/corrections/'):_absence(entries,path)
        else:
            raw=_snapshot_blob(snapshot,entries,path);originals[path]=raw;evidence['evidence/snapshot/preimages/'+path]=raw
    for name,expected in reader.ORIGINAL_BLOBS.items():
        if _oid(originals['gaza/editions/2026-10-25/'+name],'blob')!=expected:raise Error('original edition snapshot differs from approved original')
    for path,expected in authority.PUBLICATION_AGGREGATE_PREIMAGES.items():
        if _sha(originals[path])!=expected:raise Error('aggregate preimage differs from exact reviewed snapshot')
    for suffix in ('.json','-transcript.html','.mp3'):_absence(entries,'gaza/audio/2026-10-25'+suffix)
    for path in proof.NO_AUDIO_AGGREGATE_PATHS:
        raw=_snapshot_blob(snapshot,entries,path);evidence['evidence/snapshot/preimages/'+path]=raw
        try:
            if any('2026-10-25' in text for text in proof._decoded_aggregate_variants(raw.decode('utf-8-sig'),Path(path))):raise Error('dated audio reference exists in snapshot')
        except (proof.Error,ValueError,UnicodeError) as exc:raise Error('snapshot audio aggregate proof failed') from exc
    return tree_oid,entries,originals,evidence

def verify_snapshot(*,snapshot_root,expected_head):
    root=private._root(snapshot_root)
    tree,entries,originals,_=_verified_snapshot(root,expected_head)
    return {'status':'verified_remote_snapshot_only','pages_head':expected_head,'tree':tree,'tree_entry_count':len(entries),'preimages_verified':len(originals),'pinned_tree_and_aggregate_audio_absence':True,'formal_approval_checked':False,**FLAGS}


def _derive(*,source_root,proposal_commit,proposal_path,approval_commit,approval_path,snapshot_root):
    source=private._root(source_root);snapshot=private._root(snapshot_root)
    p,a=authority.load_approval(source,proposal_commit,proposal_path,approval_commit,approval_path,publication=True)
    if authority._git(source,'rev-parse','HEAD').decode().strip()!=approval_commit:
        raise Error('private source HEAD must be the exact approval commit')
    private._clean(source)
    now=_now()
    if not isinstance(now,datetime) or now.tzinfo is None:raise Error('remote verifier clock must be timezone-aware')
    if now.astimezone(ZoneInfo('America/Los_Angeles')).date().isoformat()!=p['execution_local_date']:raise Error('approved Los Angeles execution date is not current')
    if authority._time(a['approved_at'])>now:raise Error('approval is in the future')
    for binding in p['formal_evidence'].values():
        if authority._time(authority._json(authority._blob(source,proposal_commit,binding['path']))['reviewed_at'])>now:raise Error('technical review is in the future')
    tree_oid,entries,originals,evidence=_verified_snapshot(snapshot,p['pages_head']);outputs={}
    prefix=Path(p['review_packet_path']).parent.as_posix()
    for path,digest in authority.PUBLIC_HASHES.items():
        committed=prefix+'/public-preview/'+path
        if private._mode(source,proposal_commit,committed) is None:raise Error('approved output is absent')
        raw=authority._blob(source,proposal_commit,committed)
        if _sha(raw)!=digest:raise Error('approved output differs from fixed profile')
        outputs[path]=raw
    result_tree,updated,tree_objects=_result_tree(entries,outputs)
    manifest_rows=[{'public_path':path,'prior_git_blob_oid':entries[path]['sha'] if path in entries else None,'prior_sha256':_sha(originals[path]) if path in originals else None,'proposed_git_blob_oid':_oid(raw,'blob'),'proposed_sha256':_sha(raw),'mode':'100644'} for path,raw in sorted(outputs.items())]
    plan={'schema_version':SCHEMA,'profile':reader.PROFILE,'status':'verified_remote_snapshot_private_plan',
        'proposal_commit':proposal_commit,'proposal_path':proposal_path,'approval_commit':approval_commit,'approval_path':approval_path,
        'proposal_sha256':_sha(authority._blob(source,proposal_commit,proposal_path)),'approval_sha256':_sha(authority._blob(source,approval_commit,approval_path)),
        'pages_head':p['pages_head'],'original_tree':tree_oid,'result_tree':result_tree,'execution_local_date':p['execution_local_date'],
        'representations':manifest_rows,'untouched_leaf_count':sum(r['type']!='tree' and path not in outputs for path,r in entries.items()),
        'untouched_leaf_modes_and_oids_proven_equal':True,'pinned_tree_and_aggregate_audio_absence':True,
        'live_worktree_check_required':False,'route':'remote_git_objects_only_no_checkout_executor',
        'ref_update_constraints':{'repository_full_name':REPOSITORY,'branch_name':'gh-pages','expected_sha':p['pages_head'],'force':False,'required_new_commit_parent':p['pages_head'],'required_new_commit_tree':result_tree},
        'actual_cas_still_required':True,**FLAGS}
    files={'plan.json':_json(plan),'evidence/proposal.json':authority._blob(source,proposal_commit,proposal_path),'evidence/approval.json':authority._blob(source,approval_commit,approval_path),**evidence}
    files.update({'proposed/'+path:raw for path,raw in outputs.items()})
    operations=[]
    for path,raw in sorted(outputs.items()):
        operation={'public_path':path,'action':'restore_exact_preimage' if path in originals else 'remove_new_path','expected_postimage_oid':_oid(raw,'blob'),'expected_postimage_sha256':_sha(raw),'expected_postimage_mode':'100644','preimage_path':None}
        if path in originals:
            name='rollback/preimages/'+path;files[name]=originals[path];operation.update(preimage_path=name,preimage_sha256=_sha(originals[path]),preimage_oid=entries[path]['sha'],preimage_mode=entries[path]['mode'])
        operations.append(operation)
    files['rollback_manifest.json']=_json({'schema_version':'gaza_oct25_remote_inert_rollback_v1','original_pages_head':p['pages_head'],'operations':operations,'execution_authorized':False,'required_future_gates':['separate_rollback_authorization','observed_actual_publication_commit','exact_current_postimages','branch_compare_and_swap'],**FLAGS})
    for oid,raw in tree_objects.items():files['evidence/result-trees/'+oid]=raw
    packet={'schema_version':'gaza_oct25_remote_private_packet_v1','plan_sha256':_sha(files['plan.json']),'artifact_hashes':{path:_sha(raw) for path,raw in sorted(files.items())},**FLAGS,'packet_sha256':''}
    packet['packet_sha256']=legacy.fingerprint_payload(packet,'packet_sha256');files['packet.json']=_json(packet)
    return plan,packet,files

def plan(**kwargs):return _derive(**kwargs)[0]

def _bound(plan,source_root,snapshot_root):
    keys=('proposal_commit','proposal_path','approval_commit','approval_path')
    if not isinstance(plan,dict) or any(k not in plan for k in keys):raise Error('remote plan lacks evidence locators')
    expected,packet,files=_derive(source_root=source_root,snapshot_root=snapshot_root,**{k:plan[k] for k in keys})
    if not _same(plan,expected):raise Error('remote plan differs from committed authority and verified snapshot')
    return packet,files

def stage(*,plan,source_root,snapshot_root,output_root):
    source=private._root(source_root);snapshot=private._root(snapshot_root);output=private._root(output_root,required=False)
    if any(output==root or root in output.parents for root in (source,snapshot)):raise Error('private output must be outside input roots')
    packet,files=_bound(plan,source,snapshot);target=output/('remote-plan-'+packet['packet_sha256'].removeprefix('sha256:'))
    if target.exists() or target.is_symlink():
        # Idempotency proves private bytes only; do not invent a remote-head observation.
        private._inventory(private._root(target),files)
        return {'status':'idempotent_noop','package_path':str(target),**FLAGS}
    output.mkdir(parents=True,exist_ok=True);temporary=Path(tempfile.mkdtemp(prefix='.remote-plan-',dir=output))
    try:
        for path,raw in files.items():
            dest=temporary/path;dest.parent.mkdir(parents=True,exist_ok=True);dest.write_bytes(raw)
        _,again=_bound(plan,source,snapshot)
        if files!=again:raise Error('remote plan inputs drifted during private staging')
        private._inventory(temporary,files)
        if target.exists() or target.is_symlink():raise Error('conflicting private packet appeared')
        os.replace(temporary,target)
    except Exception:shutil.rmtree(temporary,ignore_errors=True);raise
    return {'status':'remote_private_plan_staged','package_path':str(target),**FLAGS}

def verify(*,plan,source_root,snapshot_root,package_root,observed_remote_head):
    authority._oid(observed_remote_head)
    packet,files=_bound(plan,source_root,snapshot_root)
    if observed_remote_head!=plan['pages_head']:raise Error('observed remote branch head is stale relative to approved snapshot')
    private._inventory(private._root(package_root),files)
    return {'status':'verified_remote_private_plan','packet_sha256':packet['packet_sha256'],'ref_update_constraints':plan['ref_update_constraints'],'actual_cas_still_required':True,**FLAGS}
