"""Private exact-profile packaging only. No publish, approval or rollback API.

All authority is re-derived from committed formal evidence. Rollback records
are inert evidence, not permission to execute or remove a public path.
"""
from __future__ import annotations
import hashlib
import os
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo
from . import gaza_correction_activation as activation
from . import gaza_corrected_manifest_reader as reader
from . import gaza_correction_proofs as inventory
from . import gaza_historical_correction as legacy

Error=activation.Error
PLAN_SCHEMA='gaza_oct25_private_release_plan_v1'
PACKAGE_SCHEMA='gaza_oct25_private_release_package_v1'
ROLLBACK_SCHEMA='gaza_oct25_inert_rollback_evidence_v1'
FALSE_AUTHORITY={'publication_authorized':False,'activation_authorized':False,
    'audio_authorized':False,'social_authorized':False,'rollback_execution_authorized':False}

def _now():return datetime.now(timezone.utc)
def _sha(raw):return hashlib.sha256(raw).hexdigest()
def _json(value):return legacy._json_document(value)
def _same(a,b):return legacy._canonical_bytes(a)==legacy._canonical_bytes(b)

def _root(path,*,required=True):
    path=Path(path).absolute()
    for component in [*reversed(path.parents),path]:
        try:component.lstat()
        except FileNotFoundError:
            if required:raise Error('required package root is missing')
            continue
        reader._check(component,directory=True)
    return path

def _mode(root,revision,path):
    activation._oid(revision);activation._path(path)
    raw=activation._git(root,'ls-tree','-z',revision,'--',path)
    if not raw:return None
    entries=raw.rstrip(b'\0').split(b'\0')
    if len(entries)!=1:raise Error('public path tree entry is ambiguous')
    meta,name=entries[0].split(b'\t',1)
    mode,kind,oid=meta.split()
    if name.decode()!=path or mode!=b'100644' or kind!=b'blob':
        raise Error('package profile requires regular nonexecutable files')
    return {'mode':mode.decode(),'git_blob_oid':oid.decode()}

def _clean(root):
    if activation._git(root,'status','--porcelain=v1','--untracked-files=all').strip():
        raise Error('package repository worktree/index is not clean')

def _inventory(root,files):
    try:
        inventory._inventory(root,files)
    except (inventory.Error,OSError) as exc:
        raise Error('private package inventory or bytes differ') from exc
    for relative in files:
        if (root/relative).stat().st_mode & 0o111:
            raise Error('private package unexpectedly contains executable file mode')


def _derive(*,source_root,pages_root,proposal_commit,proposal_path,approval_commit,approval_path):
    source=_root(source_root);pages=_root(pages_root)
    activation._oid(proposal_commit);activation._oid(approval_commit)
    activation._path(proposal_path);activation._path(approval_path)
    # Conditional publication authority is deliberately unsupported here.
    proposal,approval=activation.load_approval(source,proposal_commit,proposal_path,approval_commit,approval_path,publication=False)
    if activation._git(source,'rev-parse','HEAD').decode().strip()!=approval_commit:
        raise Error('source HEAD must be the exact package-only approval commit')
    if activation._git(pages,'rev-parse','HEAD').decode().strip()!=proposal['pages_head']:
        raise Error('Pages HEAD differs from approved prepublication head')
    _clean(source);_clean(pages)
    now=_now()
    if not isinstance(now,datetime) or now.tzinfo is None:raise Error('package clock must be timezone-aware')
    if now.astimezone(ZoneInfo('America/Los_Angeles')).date().isoformat()!=proposal['execution_local_date']:
        raise Error('approved Los Angeles execution date has expired or is not current')
    if activation._time(approval['approved_at'])>now:raise Error('package approval is in the future')
    for binding in proposal['formal_evidence'].values():
        evidence=activation._json(activation._blob(source,proposal_commit,binding['path']))
        if activation._time(evidence['reviewed_at'])>now:raise Error('technical review is in the future')
    activation._git(pages,'merge-base','--is-ancestor',activation.REVIEW_ANCHOR,proposal['pages_head'])
    activation._publication_preimages(pages,proposal['pages_head'])
    activation._audio_absence(pages,proposal['pages_head'])
    review_prefix=Path(proposal['review_packet_path']).parent.as_posix()
    representations=[];rollback=[];payloads={}
    for path,expected in sorted(activation.PUBLIC_HASHES.items()):
        committed=review_prefix+'/public-preview/'+path
        _mode(source,proposal_commit,committed)
        proposed=activation._blob(source,proposal_commit,committed)
        if _sha(proposed)!=expected:raise Error('committed public materialization differs from exact approved profile')
        before=_mode(pages,proposal['pages_head'],path)
        is_new=path.startswith('gaza/corrections/')
        if (before is None)!=is_new:raise Error('package existing/new path classification differs')
        row={'public_path':path,'proposed_sha256':expected,'proposed_mode':'100644','prior_sha256':None,'prior_git_blob_oid':None,'prior_mode':None}
        op={'public_path':path,'action':'remove_new_path' if is_new else 'restore_exact_preimage','expected_postimage_sha256':expected,'expected_postimage_mode':'100644','preimage_path':None,'preimage_sha256':None,'preimage_git_blob_oid':None,'preimage_mode':None}
        if before:
            original=activation._blob(pages,proposal['pages_head'],path)
            if reader._read_regular(pages,path)!=original:raise Error('public working preimage differs')
            row.update(prior_sha256=_sha(original),prior_git_blob_oid=before['git_blob_oid'],prior_mode=before['mode'])
            name='rollback/preimages/'+path;payloads[name]=original
            op.update(preimage_path=name,preimage_sha256=_sha(original),preimage_git_blob_oid=before['git_blob_oid'],preimage_mode=before['mode'])
        else:
            lexical=pages/path
            # Includes ignored/dangling entries and redirected ancestors.
            from . import gaza_correction_proofs as edition
            edition._lexical_repo_path(pages,path)
            if lexical.exists() or lexical.is_symlink():raise Error('new public correction path already exists locally')
        payloads['proposed/'+path]=proposed;representations.append(row);rollback.append(op)
    plan={'schema_version':PLAN_SCHEMA,'profile':reader.PROFILE,'status':'validated_package_only_plan',
        'proposal_commit':proposal_commit,'proposal_path':proposal_path,'approval_commit':approval_commit,'approval_path':approval_path,
        'proposal_sha256':_sha(activation._blob(source,proposal_commit,proposal_path)),
        'approval_sha256':_sha(activation._blob(source,approval_commit,approval_path)),
        'pages_head':proposal['pages_head'],'execution_local_date':proposal['execution_local_date'],
        'representations':representations,'package_authorized':True,**FALSE_AUTHORITY}
    rollback_manifest={'schema_version':ROLLBACK_SCHEMA,'profile':reader.PROFILE,'original_pages_head':plan['pages_head'],
        'operations':rollback,'execution_authorized':False,
        'required_future_gates':['separate_explicit_rollback_authorization','verified_actual_publication_commit','exact_current_postimages_and_modes','reconcile_any_uncertain_remote_result'],**FALSE_AUTHORITY}
    payloads['plan.json']=_json(plan);payloads['rollback_manifest.json']=_json(rollback_manifest)
    payloads['evidence/proposal.json']=activation._blob(source,proposal_commit,proposal_path)
    payloads['evidence/approval.json']=activation._blob(source,approval_commit,approval_path)
    manifest={'schema_version':PACKAGE_SCHEMA,'profile':reader.PROFILE,'status':'private_package_only',
        'plan_sha256':_sha(payloads['plan.json']),'proposal_commit':proposal_commit,'approval_commit':approval_commit,
        'pages_head':plan['pages_head'],'execution_local_date':plan['execution_local_date'],
        'artifact_hashes':{name:_sha(raw) for name,raw in sorted(payloads.items())},
        'package_authorized':True,**FALSE_AUTHORITY,'package_manifest_sha256':''}
    manifest['package_manifest_sha256']=legacy.fingerprint_payload(manifest,'package_manifest_sha256')
    payloads['package_manifest.json']=_json(manifest)
    return plan,manifest,payloads

def plan(**kwargs):
    value,_,_=_derive(**kwargs)
    return value

def _bound(plan,source_root,pages_root):
    if not isinstance(plan,dict):raise Error('package plan must be an object')
    keys=('proposal_commit','proposal_path','approval_commit','approval_path')
    if any(key not in plan for key in keys):raise Error('package plan lacks immutable evidence locators')
    expected,manifest,files=_derive(source_root=source_root,pages_root=pages_root,**{key:plan[key] for key in keys})
    if not _same(plan,expected):raise Error('package plan differs from committed authority')
    return expected,manifest,files

def stage(*,plan,source_root,pages_root,output_root):
    source=_root(source_root);pages=_root(pages_root);output=_root(output_root,required=False)
    for repo in (source,pages):
        if output==repo or repo in output.parents:raise Error('private package output must be outside repositories')
    _,manifest,files=_bound(plan,source,pages)
    target=output/('gaza-oct25-package-'+manifest['package_manifest_sha256'].removeprefix('sha256:'))
    if target.exists() or target.is_symlink():
        verify(plan=plan,package_root=target,source_root=source,pages_root=pages)
        return {'status':'idempotent_noop','package_path':str(target),'package_manifest_sha256':manifest['package_manifest_sha256'],**FALSE_AUTHORITY}
    output.mkdir(parents=True,exist_ok=True)
    temporary=Path(tempfile.mkdtemp(prefix='.package-',dir=output))
    try:
        for relative,raw in files.items():
            path=temporary/relative;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(raw)
        _,again,repeated=_bound(plan,source,pages)
        if not _same(manifest,again) or files!=repeated:raise Error('package dependencies drifted during staging')
        _inventory(temporary,files)
        if target.exists() or target.is_symlink():raise Error('conflicting package appeared during staging')
        os.replace(temporary,target)
    except Exception:
        shutil.rmtree(temporary,ignore_errors=True);raise
    return {'status':'private_release_package_staged','package_path':str(target),'package_manifest_sha256':manifest['package_manifest_sha256'],**FALSE_AUTHORITY}

def verify(*,plan,package_root,source_root,pages_root):
    root=_root(package_root)
    _,manifest,files=_bound(plan,source_root,pages_root)
    _inventory(root,files)
    return {'status':'verified_private_release_package','package_manifest_sha256':manifest['package_manifest_sha256'],**FALSE_AUTHORITY}
