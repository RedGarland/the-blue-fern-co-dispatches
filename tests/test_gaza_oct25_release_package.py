"""Disposable synthetic package approvals only; no actual release authority."""
import copy,json,hashlib
from datetime import datetime,timezone
from pathlib import Path
import pytest
import test_gaza_correction_activation as fixtures
from bluefern_dispatches import gaza_oct25_release_package as adapter
from bluefern_dispatches import gaza_correction_activation as activation
activated=fixtures.activated

@pytest.fixture
def context(activated,monkeypatch,tmp_path):
    root=activated;registry=json.loads((root/activation.REGISTRY_PATH).read_bytes())
    pc=registry['proposal_commit'];proposal=json.loads((root/'proposal.json').read_bytes())
    approval=json.loads((root/registry['approval_path']).read_bytes());approval['publication_authorized']=False
    fixtures.git(root,'checkout','--detach',pc)
    fixtures.write(root,registry['approval_path'],approval);ac=fixtures.commit(root,'SYNTHETIC package-only approval')
    pages=root/'bluefern-dispatches-pages';fixtures.git(pages,'reset','--hard',proposal['pages_head'])
    monkeypatch.setattr(adapter,'_now',lambda:datetime(2026,10,28,3,2,tzinfo=timezone.utc),raising=False)
    return dict(source_root=root,pages_root=pages,proposal_commit=pc,proposal_path='proposal.json',approval_commit=ac,approval_path=registry['approval_path'],output_root=tmp_path/'private-packages')

def make_plan(context):return adapter.plan(**{k:v for k,v in context.items() if k!='output_root'})
def stage(context,plan=None):return adapter.stage(plan=plan or make_plan(context),source_root=context['source_root'],pages_root=context['pages_root'],output_root=context['output_root'])
def verify(context,result,plan=None):return adapter.verify(plan=plan or make_plan(context),package_root=Path(result['package_path']),source_root=context['source_root'],pages_root=context['pages_root'])

def test_private_package_contract_exact_paths_and_rollback(context):
    p=make_plan(context);assert p['publication_authorized'] is False
    assert {r['public_path'] for r in p['representations']}==set(activation.PUBLIC_HASHES)
    result=stage(context,p);assert verify(context,result,p)['status']=='verified_private_release_package'
    root=Path(result['package_path']);rollback=json.loads((root/'rollback_manifest.json').read_bytes())
    assert len([r for r in rollback['operations'] if r['action']=='restore_exact_preimage'])==9
    assert len([r for r in rollback['operations'] if r['action']=='remove_new_path'])==2
    assert rollback['execution_authorized'] is False
    assert stage(context,p)['status']=='idempotent_noop'
    assert fixtures.git(context['pages_root'],'rev-parse','HEAD')==p['pages_head']
    assert not (context['source_root']/activation.REGISTRY_PATH).exists()

@pytest.mark.parametrize('mutation',['path','hash','extra_row','missing_row','authority','zero','locator','date','head'])
def test_mutable_plan_never_grants_authority(context,mutation):
    p=make_plan(context)
    if mutation=='path':p['representations'][0]['public_path']='gaza/audio/2026-10-25.mp3'
    elif mutation=='hash':p['representations'][0]['proposed_sha256']='0'*64
    elif mutation=='extra_row':p['representations'].append(copy.deepcopy(p['representations'][0]))
    elif mutation=='missing_row':p['representations'].pop()
    elif mutation=='authority':p['publication_authorized']=True
    elif mutation=='zero':p['publication_authorized']=0
    elif mutation=='locator':p.pop('proposal_commit')
    elif mutation=='date':p['execution_local_date']='2099-01-01'
    else:p['pages_head']='a'*40
    with pytest.raises(adapter.Error):stage(context,p)
    assert not context['output_root'].exists()

@pytest.mark.parametrize('relative',['proposed/gaza/editions/2026-10-25/index.html','rollback/preimages/gaza/index.html',
 'rollback_manifest.json','plan.json','evidence/approval.json','package_manifest.json'])
def test_rehashed_staged_or_rollback_bytes_not_authority(context,relative):
    p=make_plan(context);result=stage(context,p);root=Path(result['package_path'])
    (root/relative).write_bytes(b'FORGED')
    manifest=json.loads((root/'package_manifest.json').read_bytes()) if relative!='package_manifest.json' else {}
    if manifest:
        manifest['artifact_hashes'][relative]=hashlib.sha256(b'FORGED').hexdigest()
        manifest['package_manifest_sha256']=adapter.legacy.fingerprint_payload(manifest,'package_manifest_sha256')
        (root/'package_manifest.json').write_bytes(adapter._json(manifest))
    with pytest.raises(adapter.Error):verify(context,result,p)
    with pytest.raises(adapter.Error):stage(context,p)

@pytest.mark.parametrize('kind',['extra_audio','directory','dangling','executable'])
def test_exact_package_inventory_and_modes(context,kind):
    p=make_plan(context);result=stage(context,p);root=Path(result['package_path'])
    if kind=='extra_audio':(root/'audio.mp3').write_bytes(b'ID3')
    elif kind=='directory':(root/'extra').mkdir()
    elif kind=='dangling':(root/'link').symlink_to('missing')
    else:
        path=root/'proposed/gaza/index.html';path.chmod(path.stat().st_mode|0o111)
    with pytest.raises(adapter.Error):verify(context,result,p)

@pytest.mark.parametrize('mutation',['source_head','pages_head','source_dirty','public_dirty','new_file','new_link','ignored_audio','audio_reference','malformed_audio'])
def test_dependency_drift_rechecked_before_writes(context,mutation):
    p=make_plan(context);source=context['source_root'];pages=context['pages_root']
    if mutation=='source_head':fixtures.git(source,'commit','--allow-empty','-m','SYNTHETIC later source')
    elif mutation=='pages_head':fixtures.git(pages,'commit','--allow-empty','-m','SYNTHETIC later Pages')
    elif mutation=='source_dirty':(source/'proposal.json').write_bytes(b'changed')
    elif mutation=='public_dirty':(pages/'gaza/index.html').write_bytes(b'changed')
    elif mutation in ('new_file','new_link'):
        path=pages/adapter.reader.CORRECTION_PATH;path.parent.mkdir(parents=True,exist_ok=True)
        if mutation=='new_file':path.write_bytes(b'{}')
        else:path.symlink_to('missing')
    elif mutation=='ignored_audio':
        with (pages/'.git/info/exclude').open('a') as stream:stream.write('\ngaza/audio/2026-10-25.mp3\n')
        (pages/'gaza/audio/2026-10-25.mp3').write_bytes(b'ID3-ignored')
    elif mutation=='audio_reference':(pages/'gaza/podcast.xml').write_text('<rss><channel><link>2026-10-25</link></channel></rss>')
    else:(pages/'gaza/podcast.xml').write_text('<broken')
    with pytest.raises(adapter.Error):stage(context,p)
    assert not context['output_root'].exists()

@pytest.mark.parametrize('when',[datetime(2026,10,27,3,2,tzinfo=timezone.utc),datetime(2026,10,28,7,0,tzinfo=timezone.utc)])
def test_local_execution_date_is_actual_not_caller_claim(context,monkeypatch,when):
    monkeypatch.setattr(adapter,'_now',lambda:when)
    with pytest.raises(adapter.Error):make_plan(context)
    assert not context['output_root'].exists()


def test_midnight_rollover_during_stage_cleans_temporary_output(context,monkeypatch):
    p=make_plan(context)
    ticks=iter([datetime(2026,10,28,6,59,tzinfo=timezone.utc),datetime(2026,10,28,7,0,tzinfo=timezone.utc)])
    monkeypatch.setattr(adapter,'_now',lambda:next(ticks))
    with pytest.raises(adapter.Error,match='execution date'):stage(context,p)
    assert list(context['output_root'].iterdir())==[]


def test_atomic_install_failure_leaves_no_package(context,monkeypatch):
    p=make_plan(context)
    def fail(*args):raise OSError('Synthetic interrupted install')
    monkeypatch.setattr(adapter.os,'replace',fail)
    with pytest.raises(OSError):stage(context,p)
    assert list(context['output_root'].iterdir())==[]

@pytest.mark.parametrize('root_key',['source_root','pages_root'])
def test_output_inside_repositories_rejected(context,root_key):
    p=make_plan(context);context['output_root']=context[root_key]/'forbidden-packages'
    with pytest.raises(adapter.Error):stage(context,p)
    assert not context['output_root'].exists()


def test_pending_review_packet_is_not_formal_authority(context):
    context['proposal_path']='review/packet.json'
    with pytest.raises(adapter.Error):make_plan(context)


def test_publication_authorized_approval_is_not_package_only(context):
    source=context['source_root'];a=json.loads(activation._blob(source,context['approval_commit'],context['approval_path']))
    fixtures.git(source,'checkout','--detach',context['proposal_commit']);a['publication_authorized']=True
    fixtures.write(source,context['approval_path'],a);context['approval_commit']=fixtures.commit(source,'SYNTHETIC publication scope is not adapter scope')
    with pytest.raises(adapter.Error):make_plan(context)


def test_later_verification_rechecks_heads_and_date(context,monkeypatch):
    p=make_plan(context);result=stage(context,p)
    monkeypatch.setattr(adapter,'_now',lambda:datetime(2026,10,28,7,0,tzinfo=timezone.utc))
    with pytest.raises(adapter.Error):verify(context,result,p)
