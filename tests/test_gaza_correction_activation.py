"""Self-contained fictional evidence; never reads private review artifacts."""
import hashlib,json,shutil,subprocess
from pathlib import Path
import pytest
from bluefern_dispatches import gaza_correction_activation as activation
from bluefern_dispatches import gaza_corrected_manifest_reader as reader
from test_gaza_correction_fixture_support import make_context,git,init,put,encode

def commit(root,label='SYNTHETIC TEST ONLY'):
    git(root,'add','.');git(root,'commit','-m',label);return git(root,'rev-parse','HEAD')
def write(root,path,obj):return put(root,path,encode(obj))
@pytest.fixture
def activated(tmp_path,monkeypatch,request):
    return make_context(tmp_path,monkeypatch,publication=True,parent_mutation=getattr(request,'param',None))['source_root']

def test_real_git_proof_accepts_only_complete_synthetic_context(activated):
    result=activation.load_activation(activated)
    assert result['profile']==reader.PROFILE
    assert reader.corrected_profile_for_outputs(activated,'2026-10-28').is_file()

@pytest.mark.parametrize('mutation',['delete_registry','change_registry','pages_drift','committed_pages_drift','delete_pages','registry_unknown','approval_scope','proposal_binding'])
def test_activation_drift_cannot_silently_deactivate(activated,mutation):
    root=activated;p=root/activation.REGISTRY_PATH
    if mutation=='delete_registry':p.unlink()
    elif mutation=='change_registry':p.write_bytes(p.read_bytes()+b' ')
    elif mutation in ('pages_drift','committed_pages_drift'):
        target=root/'bluefern-dispatches-pages'/next(iter(reader.PROFILE_HASHES));target.write_bytes(target.read_bytes()+b' ')
        if mutation=='committed_pages_drift':commit(root/'bluefern-dispatches-pages','SYNTHETIC drift')
    elif mutation=='delete_pages':shutil.rmtree(root/'bluefern-dispatches-pages')
    else:
        r=json.loads(p.read_bytes())
        if mutation=='registry_unknown':r['state']='unknown'
        elif mutation=='approval_scope':r['approval_commit']=r['proposal_commit']
        else:r['proposal_path']='other.json'
        write(root,activation.REGISTRY_PATH,r);commit(root,'SYNTHETIC invalid registry')
    with pytest.raises(reader.CorrectedManifestError):activation.load_activation(root)

def test_fresh_or_ordinary_checkout_stays_inactive(tmp_path):
    assert activation.load_activation(tmp_path) is None
    root=tmp_path/'ordinary';init(root);(root/'ordinary.txt').write_text('normal');commit(root,'normal')
    assert activation.load_activation(root) is None
    assert reader.corrected_profile_for_outputs(root,'2026-10-28') is None

def test_untracked_registry_is_not_authority(tmp_path):
    write(tmp_path,activation.REGISTRY_PATH,{})
    with pytest.raises(reader.CorrectedManifestError):activation.load_activation(tmp_path)


def test_committed_registry_deletion_fails_closed(activated):
    (activated/activation.REGISTRY_PATH).unlink();commit(activated,'SYNTHETIC deletion')
    with pytest.raises(activation.Error,match='deletion'):activation.load_activation(activated)

@pytest.mark.parametrize('kind',['file','directory','dangling_link','committed_file','reference','malformed'])
def test_active_noaudio_revalidated(activated,kind):
    pages=activated/'bluefern-dispatches-pages';path=pages/'gaza/audio/2026-10-25.mp3'
    if kind=='file':path.write_bytes(b'ID3')
    elif kind=='directory':path.mkdir()
    elif kind=='dangling_link':path.symlink_to('missing')
    elif kind=='committed_file':path.write_bytes(b'ID3');commit(pages,'SYNTHETIC audio drift')
    elif kind=='reference':(pages/'gaza/podcast.xml').write_text('<rss><channel><item><link>2026-10-25</link></item></channel></rss>')
    else:(pages/'gaza/podcast.xml').write_text('<rss broken')
    with pytest.raises(activation.Error):activation.load_activation(activated)

@pytest.mark.parametrize('path',['gaza/index.html','gaza/archive.html','gaza/rss.xml'])
@pytest.mark.parametrize('committed',[False,True])
def test_owning_aggregate_drift_rejected(activated,path,committed):
    pages=activated/'bluefern-dispatches-pages';target=pages/path
    target.write_bytes(target.read_bytes().replace(b'Fictional narrowed headline',b'Incorrect changed title'))
    if committed:commit(pages,'SYNTHETIC aggregate drift')
    with pytest.raises(activation.Error):activation.load_activation(activated)


def test_later_unrelated_rows_and_items_remain_allowed_repeatedly(activated):
    pages=activated/'bluefern-dispatches-pages'
    for date in ('2026-10-28','2026-10-29'):
        for rel in ('gaza/index.html','gaza/archive.html'):
            p=pages/rel;raw=p.read_bytes();p.write_bytes(raw.replace(b'</ul>',('<li><a href="editions/'+date+'/">Later unrelated report</a></li></ul>').encode(),1))
        p=pages/'gaza/rss.xml';p.write_bytes(p.read_bytes().replace(b'</channel>',('<item><title>Later report</title><guid>'+date+'</guid></item></channel>').encode()))
        commit(pages,'SYNTHETIC later unrelated edition')
        assert activation.load_activation(activated)['profile']==reader.PROFILE
        assert activation.load_activation(activated)['profile']==reader.PROFILE

@pytest.mark.parametrize('field,value',[('reviewed_at',True),('reviewed_at','2026-10-28'),('reviewer',{}),('lineage',{})])
def test_formal_evidence_validation_rejects_bad_committed_values(activated,field,value):
    root=activated;path='evidence/source_lineage.json';obj=json.loads((root/path).read_bytes());obj[field]=value
    digest=write(root,path,obj);proposal=json.loads((root/'proposal.json').read_bytes());proposal['formal_evidence']['source_lineage']['sha256']=digest
    write(root,'proposal.json',proposal);head=commit(root,'SYNTHETIC invalid formal evidence')
    with pytest.raises(activation.Error):activation.load_formal_proposal(root,head,'proposal.json')


def test_editorial_reviewer_cannot_be_independent_auditor(activated):
    root=activated;path='evidence/independent_audit.json';obj=json.loads((root/path).read_bytes());obj['reviewer']='SYNTHETIC editorial_decision'
    digest=write(root,path,obj);proposal=json.loads((root/'proposal.json').read_bytes());proposal['formal_evidence']['independent_audit']['sha256']=digest
    write(root,'proposal.json',proposal);head=commit(root,'SYNTHETIC nonindependent audit')
    with pytest.raises(activation.Error,match='self-approved'):activation.load_formal_proposal(root,head,'proposal.json')

@pytest.mark.parametrize('key,value',[('approval_commit','--output=unsafe'),('proposal_commit','HEAD'),('approval_path','../bad'),('proposal_path','./bad')])
def test_invalid_helper_arguments_rejected_before_git(tmp_path,monkeypatch,key,value):
    args=dict(source=tmp_path,proposal_commit='a'*40,proposal_path='proposal.json',approval_commit='b'*40,approval_path='approvals/gaza/test.json',publication=True);args[key]=value
    def forbidden(*a):raise AssertionError('Git was invoked for invalid input')
    monkeypatch.setattr(activation,'_git',forbidden)
    with pytest.raises(activation.Error):activation.load_approval(**args)


@pytest.mark.parametrize('activated',['parent_original','parent_correction','parent_correction_tree','parent_correction_link'],indirect=True)
def test_publication_parent_preimages_and_new_paths_are_exact(activated):
    with pytest.raises(activation.Error,match='parent'):activation.load_activation(activated)


def test_ignored_dated_audio_is_not_absence(activated):
    pages=activated/'bluefern-dispatches-pages'
    with (pages/'.git/info/exclude').open('a') as stream:stream.write('\ngaza/audio/2026-10-25.mp3\n')
    (pages/'gaza/audio/2026-10-25.mp3').write_bytes(b'ID3-ignored')
    assert '2026-10-25.mp3' not in git(pages,'status','--porcelain')
    with pytest.raises(activation.Error):activation.load_activation(activated)

@pytest.mark.parametrize('field,value',[('approver',{}),('approved_at',True),('approved_at','not-time'),('approved_at','2026-10-28T02:59:59Z')])
def test_bad_approval_identity_time_or_order_rejected(activated,field,value):
    root=activated;registry=json.loads((root/activation.REGISTRY_PATH).read_bytes());pc=registry['proposal_commit'];ap=registry['approval_path']
    approval=json.loads((root/ap).read_bytes());approval[field]=value
    git(root,'checkout','--detach',pc);write(root,ap,approval);ac=commit(root,'SYNTHETIC invalid approval')
    with pytest.raises(activation.Error):activation.load_approval(root,pc,'proposal.json',ac,ap,publication=True)

@pytest.mark.parametrize('activated',['parent_neighbor:gaza/index.html','parent_neighbor:gaza/archive.html','parent_neighbor:gaza/rss.xml'],indirect=True)
def test_publication_cannot_erase_newer_neighbor_from_parent(activated):
    with pytest.raises(activation.Error,match='aggregate preimage'):activation.load_activation(activated)


@pytest.fixture
def public_active(tmp_path,monkeypatch):
    """Fictional public history; source HEAD contains no approval ancestry."""
    c=make_context(tmp_path,monkeypatch,publication=True)
    root=c['source_root'];pages=c['pages_root']
    published=git(pages,'rev-parse','HEAD');parent=git(pages,'rev-parse','HEAD^')
    monkeypatch.setattr(activation,'PUBLISHED_COMMIT',published)
    monkeypatch.setattr(activation,'PUBLISHED_PARENT',parent)
    monkeypatch.setattr(activation,'PUBLISHED_TREE',git(pages,'rev-parse','HEAD^{tree}'))
    monkeypatch.setattr(activation,'REVIEW_PACKET_SHA',None)
    git(root,'checkout','--orphan','synthetic-public-runtime')
    git(root,'rm','-rf','.')
    (root/'.gitignore').write_text('bluefern-dispatches-pages/\n')
    write(root,activation.REGISTRY_PATH,dict(schema_version=activation.PUBLIC_REGISTRY_SCHEMA,
        state='active',profile=reader.PROFILE,published_pages_commit=published))
    commit(root,'SYNTHETIC explicit public-only activation')
    def forbidden(*args,**kwargs):raise AssertionError('public runtime must not consult private approvals')
    monkeypatch.setattr(activation,'load_approval',forbidden)
    return root


def test_public_history_activation_needs_no_private_ancestry(public_active):
    assert len(git(public_active,'rev-list','HEAD').splitlines()) == 1
    result=activation.load_activation(public_active)
    assert result['authority_basis']=='fixed_public_history'
    assert result['publication_authorized'] is False
    assert 'approval_commit' not in result
    assert reader.corrected_profile_for_outputs(public_active,'2026-10-28').is_file()
    assert activation.load_activation(public_active)==result


@pytest.mark.parametrize('mutation',['registry_deleted','registry_edited','registry_committed_deleted',
    'unknown','private_locator','wrong_publication','wrong_state','wrong_profile','pages_missing',
    'older_pages','committed_profile_drift','working_profile_drift','owning_aggregate','audio_file',
    'ignored_audio_link','wrong_tree','wrong_parent','wrong_hash','wrong_path_set'])
def test_public_history_activation_refuses_drift(public_active,monkeypatch,mutation):
    root=public_active;pages=root/'bluefern-dispatches-pages';rp=root/activation.REGISTRY_PATH
    if mutation.startswith('registry_'):
        if mutation=='registry_edited':rp.write_bytes(rp.read_bytes()+b' ')
        else:
            rp.unlink()
            if mutation=='registry_committed_deleted':commit(root)
    elif mutation in ('unknown','private_locator','wrong_publication','wrong_state','wrong_profile'):
        value=json.loads(rp.read_bytes())
        if mutation=='unknown':value['schema_version']='unknown'
        elif mutation=='private_locator':value['approval_path']='fictional.json'
        elif mutation=='wrong_publication':value['published_pages_commit']='0'*40
        elif mutation=='wrong_state':value['state']='pending'
        else:value['profile']='unknown'
        write(root,activation.REGISTRY_PATH,value);commit(root)
    elif mutation=='pages_missing':shutil.rmtree(pages)
    elif mutation=='older_pages':git(pages,'checkout','--detach',activation.PUBLISHED_PARENT)
    elif mutation in ('committed_profile_drift','working_profile_drift'):
        target=pages/reader.CORRECTION_PATH;target.write_bytes(target.read_bytes()+b' ')
        if mutation=='committed_profile_drift':commit(pages)
    elif mutation=='owning_aggregate':
        p=pages/'gaza/index.html';p.write_bytes(p.read_bytes().replace(b'Fictional narrowed headline',b'Old framing'))
        commit(pages)
    elif mutation in ('audio_file','ignored_audio_link'):
        p=pages/'gaza/audio/2026-10-25.mp3';p.parent.mkdir(parents=True,exist_ok=True)
        if mutation=='audio_file':p.write_bytes(b'forbidden')
        else:
            try:p.symlink_to('nonexistent')
            except OSError:pytest.skip('symlink capability unavailable')
            (pages/'.gitignore').write_text('gaza/audio/\n')
    elif mutation=='wrong_tree':monkeypatch.setattr(activation,'PUBLISHED_TREE','0'*40)
    elif mutation=='wrong_parent':monkeypatch.setattr(activation,'PUBLISHED_PARENT','0'*40)
    elif mutation=='wrong_hash':monkeypatch.setattr(activation,'PUBLIC_HASHES',{**activation.PUBLIC_HASHES,reader.CORRECTION_PATH:'0'*64})
    else:monkeypatch.setattr(activation,'PUBLIC_HASHES',{k:v for k,v in activation.PUBLIC_HASHES.items() if k!=reader.CORRECTION_PATH})
    with pytest.raises((activation.Error,ValueError)):
        activation.load_activation(root)


def test_public_history_allows_later_editions_but_preserves_owned_projection(public_active):
    root=public_active;pages=root/'bluefern-dispatches-pages'
    before=activation.load_activation(root)
    for p in ('gaza/index.html','gaza/archive.html'):
        f=pages/p;f.write_bytes(f.read_bytes().replace(b'</ul>',b'<li><a href="editions/2026-10-29/">Later unrelated edition</a></li></ul>'))
    f=pages/'gaza/rss.xml';f.write_bytes(f.read_bytes().replace(b'</channel>',b'<item><guid>later-unrelated-edition</guid></item></channel>'))
    target=pages/'gaza/editions/2026-10-29/index.html';target.parent.mkdir(parents=True);target.write_text('Unrelated later edition')
    later=commit(pages)
    after=activation.load_activation(root)
    assert after['current_pages_head']==later and before['current_pages_head']!=later
    assert after['published_pages_commit']==before['published_pages_commit']
    assert reader.corrected_profile_for_outputs(root,'2026-10-30').is_file()


@pytest.mark.parametrize('site_state',['absent','corrected','partial','unknown'])
@pytest.mark.parametrize('dispatch_state',['absent','corrected','partial','unknown'])
@pytest.mark.parametrize('persisted',[False,True])
def test_public_history_real_activation_replica_matrix(public_active,site_state,dispatch_state,persisted):
    import itertools
    from bluefern_dispatches import story_dedupe
    root=public_active;pages=root/'bluefern-dispatches-pages'
    for rel,state in [('output/site',site_state),('output/dispatches',dispatch_state)]:
        if state=='absent':continue
        dest=root/rel
        shutil.copytree(pages,dest,ignore=shutil.ignore_patterns('.git'))
        p=dest/'gaza/editions/2026-10-25/curation_manifest.json'
        if state=='partial':p.unlink();(dest/reader.CORRECTION_PATH).unlink()
        elif state=='unknown':
            p.write_text('[{"title":"Untrusted old framing"}]');(dest/reader.CORRECTION_PATH).unlink()
    memory=root/'data/records/story_memory.json'
    if persisted:
        memory.write_text(json.dumps([{'dispatch_slug':'gaza','edition_date':'2026-10-25','story_id':'synthetic-story','title':'Old framing','summary':'Archived claim'}]))
    original_order=reader.REPLICA_ROOTS
    try:
        for order in itertools.permutations(original_order):
            reader.REPLICA_ROOTS=order
            if {'partial','unknown'} & {site_state,dispatch_state}:
                with pytest.raises(reader.CorrectedManifestError):story_dedupe.prior_memory_from_outputs(root,'gaza','2026-10-28')
            else:
                rows,_=story_dedupe.prior_memory_from_outputs(root,'gaza','2026-10-28')
                assert len(rows)==1 and rows[0]['title']=='Fictional narrowed headline'
    finally:reader.REPLICA_ROOTS=original_order


def test_public_history_does_not_silently_activate_a_fresh_source(public_active,tmp_path):
    fresh=tmp_path/'fresh-source';init(fresh)
    (fresh/'.gitignore').write_text('bluefern-dispatches-pages/\n');commit(fresh)
    shutil.copytree(public_active/'bluefern-dispatches-pages',fresh/'bluefern-dispatches-pages')
    assert activation.load_activation(fresh) is None
    with pytest.raises(reader.CorrectedManifestError,match='pending corrected profile'):
        reader.corrected_profile_for_outputs(fresh,'2026-10-28')


@pytest.mark.parametrize('fixture_name',['activated','public_active'])
@pytest.mark.parametrize('side',['working','committed'])
def test_current_profile_diagnostic_identifies_loader_side(request,fixture_name,side):
    root=request.getfixturevalue(fixture_name)
    pages=root/'bluefern-dispatches-pages'
    path=reader.CORRECTION_PATH
    target=pages/path;original=target.read_bytes()
    target.write_bytes(original+b' ')
    if side=='committed':
        commit(pages,'SYNTHETIC diagnostic committed drift')
        # Working bytes alone must not excuse drift in the current Git blob.
        target.write_bytes(original)
    with pytest.raises(activation.Error) as caught:
        activation.load_activation(root)
    assert str(caught.value)==f'current corrected Pages profile drifted ({side}): {path}'
    assert str(root) not in str(caught.value)


@pytest.mark.parametrize('side',['working','committed'])
@pytest.mark.parametrize('path',tuple(reader.PROFILE_HASHES))
def test_current_profile_diagnostic_exact_path_and_short_circuit(monkeypatch,side,path):
    paths=tuple(reader.PROFILE_HASHES)
    payloads={p:('SYNTHETIC '+p+'\n').encode() for p in paths}
    monkeypatch.setattr(reader,'PROFILE_HASHES',{p:hashlib.sha256(b).hexdigest() for p,b in payloads.items()})
    calls=[]
    def read(which,p):
        calls.append((which,p))
        return payloads[p]+(b' drift' if (which,p)==(side,path) else b'')
    monkeypatch.setattr(activation,'_blob',lambda root,head,p:read('committed',p))
    monkeypatch.setattr(reader,'_read_regular',lambda root,p:read('working',p))
    with pytest.raises(activation.Error) as caught:
        activation._validate_current_profile(Path('synthetic-pages'),'a'*40)
    assert str(caught.value)==f'current corrected Pages profile drifted ({side}): {path}'
    expected=[]
    for p in paths:
        expected.append(('committed',p))
        if (side,path)==('committed',p):break
        expected.append(('working',p))
        if (side,path)==('working',p):break
    assert calls==expected


@pytest.mark.parametrize('changed',[b'line\r\n',b'\xef\xbb\xbfline\n',b'line\n ',b'Line\n'])
def test_current_profile_diagnostic_never_normalizes_working_bytes(monkeypatch,changed):
    path=reader.CORRECTION_PATH;original=b'line\n'
    monkeypatch.setattr(reader,'PROFILE_HASHES',{path:hashlib.sha256(original).hexdigest()})
    monkeypatch.setattr(activation,'_blob',lambda *args:original)
    monkeypatch.setattr(reader,'_read_regular',lambda *args:changed)
    with pytest.raises(activation.Error) as caught:
        activation._validate_current_profile(Path('synthetic-pages'),'a'*40)
    assert str(caught.value)==f'current corrected Pages profile drifted (working): {path}'


def test_current_profile_diagnostic_preserves_read_guard_exception(monkeypatch):
    path=reader.CORRECTION_PATH;original=b'exact'
    monkeypatch.setattr(reader,'PROFILE_HASHES',{path:hashlib.sha256(original).hexdigest()})
    monkeypatch.setattr(activation,'_blob',lambda *args:original)
    failure=reader.CorrectedManifestError('corrected profile links/reparse points forbidden')
    def forbidden(*args):raise failure
    monkeypatch.setattr(reader,'_read_regular',forbidden)
    with pytest.raises(reader.CorrectedManifestError) as caught:
        activation._validate_current_profile(Path('synthetic-pages'),'a'*40)
    assert caught.value is failure


def test_current_profile_diagnostic_accepts_only_all_exact_bytes(monkeypatch):
    paths=tuple(reader.PROFILE_HASHES)
    payloads={p:('SYNTHETIC '+p+'\n').encode() for p in paths}
    monkeypatch.setattr(reader,'PROFILE_HASHES',{p:hashlib.sha256(b).hexdigest() for p,b in payloads.items()})
    calls=[]
    def read(which,p):
        calls.append((which,p));return payloads[p]
    monkeypatch.setattr(activation,'_blob',lambda root,head,p:read('committed',p))
    monkeypatch.setattr(reader,'_read_regular',lambda root,p:read('working',p))
    assert activation._validate_current_profile(Path('synthetic-pages'),'a'*40) is None
    assert calls==[(side,p) for p in paths for side in ('committed','working')]
