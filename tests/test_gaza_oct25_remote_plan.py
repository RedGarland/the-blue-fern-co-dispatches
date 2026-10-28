"""Generic disposable Git evidence, never a real approval or remote mutation."""
import copy,json,hashlib
from datetime import datetime,timezone
from pathlib import Path
import pytest
from test_gaza_correction_fixture_support import make_context,git,commit,put
from bluefern_dispatches import gaza_oct25_remote_plan as remote

def snapshot(pages,revision,root):
    root.mkdir(parents=True,exist_ok=True)
    import subprocess
    raw=subprocess.run(['git','-C',str(pages),'cat-file','commit',revision],check=True,capture_output=True).stdout
    put(root,'commit.raw',raw);tree=git(pages,'rev-parse',revision+'^{tree}')
    data=subprocess.run(['git','-C',str(pages),'ls-tree','-r','-t','-z','--full-tree',revision],check=True,capture_output=True).stdout
    entries=[]
    for line in data.split(b'\0'):
        if not line:continue
        metadata,path=line.split(b'\t',1);mode,kind,oid=metadata.decode().split()
        entries.append({'path':path.decode(),'mode':mode,'type':kind,'sha':oid})
    put(root,'tree.json',json.dumps({'sha':tree,'truncated':False,'tree':entries}).encode())
    paths={p for p in remote.authority.PUBLIC_HASHES if not p.startswith('gaza/corrections/')}|set(remote.proof.NO_AUDIO_AGGREGATE_PATHS)
    for path in paths:
        raw=subprocess.run(['git','-C',str(pages),'show',revision+':'+path],check=True,capture_output=True).stdout
        put(root,'preimages/'+path,raw)
    return revision

@pytest.fixture
def case(tmp_path,monkeypatch):
    c=make_context(tmp_path,monkeypatch,publication=True)
    proposal=json.loads(remote.authority._blob(c['source_root'],c['proposal_commit'],c['proposal_path']))
    git(c['pages_root'],'reset','--hard',proposal['pages_head'])
    git(c['source_root'],'checkout','--detach',c['approval_commit'])
    c['snapshot_root']=tmp_path/'snapshot';c['output_root']=tmp_path/'private-output';c['head']=snapshot(c['pages_root'],proposal['pages_head'],c['snapshot_root'])
    monkeypatch.setattr(remote,'_now',lambda:datetime(2026,10,28,3,2,tzinfo=timezone.utc))
    return c

def plan(c):return remote.plan(**{k:c[k] for k in ('source_root','proposal_commit','proposal_path','approval_commit','approval_path','snapshot_root')})
def stage(c,p):return remote.stage(plan=p,source_root=c['source_root'],snapshot_root=c['snapshot_root'],output_root=c['output_root'])
def verify(c,p,result,head=None):return remote.verify(plan=p,source_root=c['source_root'],snapshot_root=c['snapshot_root'],package_root=Path(result['package_path']),observed_remote_head=head or c['head'])

def test_remote_plan_proves_exact_tree_and_never_requires_runner_checkout(case):
    p=plan(case);assert p['pinned_tree_and_aggregate_audio_absence'] is True
    assert len(p['representations'])==11 and p['untouched_leaf_modes_and_oids_proven_equal'] is True
    assert p['ref_update_constraints']['branch_name']=='gh-pages' and p['ref_update_constraints']['expected_sha']==case['head']
    assert p['ref_update_constraints']['repository_full_name']=='RedGarland/the-blue-fern-co-dispatches'
    assert p['ref_update_constraints']['force'] is False and all(p[k] is False for k in remote.FLAGS)
    import shutil
    shutil.rmtree(case['pages_root']) # snapshot-only route must not inspect a runner checkout
    result=stage(case,p);assert verify(case,p,result)['status']=='verified_remote_private_plan'
    assert stage(case,p)['status']=='idempotent_noop'
    rollback=json.loads((Path(result['package_path'])/'rollback_manifest.json').read_bytes())
    assert [op['action'] for op in rollback['operations']].count('restore_exact_preimage')==9
    assert [op['action'] for op in rollback['operations']].count('remove_new_path')==2

@pytest.mark.parametrize('mutation',['missing_commit','commit_byte','missing_tree','truncated','missing_row','duplicate_row','mode','sha','missing_blob','blob_byte','neighbor_preimage'])
def test_snapshot_missing_or_tampered_proof_rejected(case,mutation):
    root=case['snapshot_root']
    if mutation=='missing_commit':(root/'commit.raw').unlink()
    elif mutation=='commit_byte':put(root,'commit.raw',(root/'commit.raw').read_bytes()+b'!')
    elif mutation=='missing_tree':(root/'tree.json').unlink()
    elif mutation in ('missing_blob','blob_byte','neighbor_preimage'):
        path=root/'preimages/gaza/index.html'
        if mutation=='missing_blob':path.unlink()
        else:path.write_bytes(path.read_bytes()+b'<li>new unapproved neighbor</li>')
    else:
        value=json.loads((root/'tree.json').read_bytes())
        if mutation=='truncated':value['truncated']=True
        elif mutation=='missing_row':value['tree'].pop()
        elif mutation=='duplicate_row':value['tree'].append(value['tree'][0])
        elif mutation=='mode':value['tree'][0]['mode']='120000'
        else:value['tree'][0]['sha']='0'*40
        put(root,'tree.json',json.dumps(value).encode())
    with pytest.raises(remote.Error):plan(case)
    assert not case['output_root'].exists()

@pytest.mark.parametrize('value,relative',[(b'<rss><link>2026&#45;10&#45;25</link></rss>','gaza/podcast.xml'),(b'{"url":"2026\\u002d10\\u002d25"}','gaza/flash-briefing.json'),(b'<html><a href="2026%2D10%2D25">x</a></html>','gaza/audio/index.html'),(b'<rss broken','gaza/podcast.xml')])
def test_valid_new_snapshot_with_encoded_audio_reference_is_refused(case,value,relative):
    put(case['pages_root'],relative,value);head=commit(case['pages_root']);snapshot(case['pages_root'],head,case['snapshot_root'])
    with pytest.raises(remote.Error):remote.verify_snapshot(snapshot_root=case['snapshot_root'],expected_head=head)

@pytest.mark.parametrize('kind',['file','directory','symlink'])
@pytest.mark.parametrize('path',['gaza/audio/2026-10-25.mp3',remote.reader.CORRECTION_PATH])
def test_absent_destinations_are_tree_proven(case,kind,path):
    dest=case['pages_root']/path;dest.parent.mkdir(parents=True,exist_ok=True)
    if kind=='file':dest.write_bytes(b'forbidden')
    elif kind=='directory':dest.mkdir();(dest/'child').write_bytes(b'forbidden')
    else:dest.symlink_to('missing')
    head=commit(case['pages_root']);snapshot(case['pages_root'],head,case['snapshot_root'])
    with pytest.raises(remote.Error):remote.verify_snapshot(snapshot_root=case['snapshot_root'],expected_head=head)

@pytest.mark.parametrize('mutation',['path','extra','hash','tree','cas','repository','authority','zero'])
def test_rehashed_plan_cannot_expand_write_set_or_authority(case,mutation):
    p=plan(case)
    if mutation=='path':p['representations'][0]['public_path']='index.html'
    elif mutation=='extra':p['representations'].append(copy.deepcopy(p['representations'][0]))
    elif mutation=='hash':p['representations'][0]['proposed_sha256']='0'*64
    elif mutation=='tree':p['result_tree']='0'*40
    elif mutation=='cas':p['ref_update_constraints']['force']=True
    elif mutation=='repository':p['ref_update_constraints']['repository_full_name']='example/unapproved-repository'
    elif mutation=='zero':p['publication_authorized']=0
    else:p['publication_authorized']=True
    with pytest.raises(remote.Error):stage(case,p)

@pytest.mark.parametrize('relative',['plan.json','rollback_manifest.json','proposed/gaza/index.html','evidence/snapshot/commit.raw','evidence/approval.json'])
def test_rehashed_private_inventory_tamper_rejected(case,relative):
    p=plan(case);result=stage(case,p);root=Path(result['package_path']);put(root,relative,b'forged')
    packet=json.loads((root/'packet.json').read_bytes());packet['artifact_hashes'][relative]=hashlib.sha256(b'forged').hexdigest();packet['packet_sha256']=remote.legacy.fingerprint_payload(packet,'packet_sha256');put(root,'packet.json',remote._json(packet))
    with pytest.raises(remote.Error):verify(case,p,result)


def test_stale_observed_remote_head_refuses_final_verification(case):
    p=plan(case);result=stage(case,p)
    with pytest.raises(remote.Error,match='stale'):verify(case,p,result,'a'*40)


def test_unrelated_leaf_modes_remain_exact(case):
    put(case['pages_root'],'unrelated/tool',b'#!/bin/sh\n');(case['pages_root']/'unrelated/tool').chmod(0o755)
    head=commit(case['pages_root']);snapshot(case['pages_root'],head,case['snapshot_root'])
    document=json.loads((case['snapshot_root']/'tree.json').read_bytes());entries=remote.validate_tree(document,document['sha'])
    outputs={p:b'synthetic changed bytes' for p in remote.authority.PUBLIC_HASHES}
    _,updated,_=remote._result_tree(entries,outputs)
    assert updated['unrelated/tool']==entries['unrelated/tool'] and updated['unrelated/tool']['mode']=='100755'


def test_result_tree_matches_independent_git_fixture_oracle(case):
    p=plan(case);result=stage(case,p);root=Path(result['package_path'])
    for row in p['representations']:
        path=row['public_path'];put(case['pages_root'],path,(root/'proposed'/path).read_bytes())
    head=commit(case['pages_root'])
    assert git(case['pages_root'],'rev-parse',head+'^{tree}')==p['result_tree']
    changed=set(git(case['pages_root'],'diff-tree','--no-commit-id','--name-only','-r',case['head'],head).splitlines())
    assert changed==set(remote.authority.PUBLIC_HASHES)


def test_valid_tree_with_new_neighbor_still_needs_new_preimage_review(case):
    path='gaza/index.html';put(case['pages_root'],path,(case['pages_root']/path).read_bytes()+b'<p>Fictional later neighbor</p>')
    head=commit(case['pages_root']);snapshot(case['pages_root'],head,case['snapshot_root'])
    with pytest.raises(remote.Error,match='aggregate preimage'):remote.verify_snapshot(snapshot_root=case['snapshot_root'],expected_head=head)
    with pytest.raises(remote.Error,match='raw commit'):plan(case)

@pytest.mark.parametrize('kind',['audio','directory','symlink','executable'])
def test_remote_private_inventory_is_exact(case,kind):
    p=plan(case);result=stage(case,p);root=Path(result['package_path'])
    if kind=='audio':put(root,'audio.mp3',b'ID3-unapproved')
    elif kind=='directory':(root/'extra').mkdir()
    elif kind=='symlink':(root/'dangling').symlink_to('missing')
    else:
        path=root/'proposed/gaza/index.html';path.chmod(path.stat().st_mode|0o111)
    with pytest.raises(remote.Error):verify(case,p,result)
    with pytest.raises(remote.Error):stage(case,p)


def test_private_idempotence_does_not_fabricate_remote_observation(case,monkeypatch):
    p=plan(case);stage(case,p)
    def forbidden(**kwargs):raise AssertionError('stage must not call final verify with a fabricated remote observation')
    monkeypatch.setattr(remote,'verify',forbidden)
    assert stage(case,p)['status']=='idempotent_noop'


@pytest.mark.parametrize('mutation',['descendant_head','tracked_dirty','untracked_dirty'])
@pytest.mark.parametrize('boundary',['plan','stage','verify'])
def test_private_authority_source_must_remain_exact_and_clean(case,mutation,boundary):
    p=plan(case);result=stage(case,p) if boundary=='verify' else None
    source=case['source_root']
    if mutation=='descendant_head':git(source,'commit','--allow-empty','-m','SYNTHETIC later private state')
    elif mutation=='tracked_dirty':put(source,case['approval_path'],b'changed approval working bytes')
    else:put(source,'untracked-approval.json',b'{}')
    with pytest.raises(remote.Error,match='HEAD|clean'):
        if boundary=='plan':plan(case)
        elif boundary=='stage':stage(case,p)
        else:verify(case,p,result)
