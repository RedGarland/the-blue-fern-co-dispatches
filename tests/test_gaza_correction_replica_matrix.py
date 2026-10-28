"""Active/inactive replica permutations over fictional generated evidence."""
import json,shutil
from pathlib import Path
from types import SimpleNamespace
import pytest
from bluefern_dispatches import gaza_corrected_manifest_reader as reader,story_dedupe as dedupe,gaza_sources
from test_gaza_correction_fixture_support import make_context
release=SimpleNamespace(SUMMARY='Fictional narrowed narrative.',rich=SimpleNamespace(BLOBS=tuple(reader.ORIGINAL_BLOBS),RETAIN='synthetic-story',WITHDRAW='synthetic-withdrawn'))
@pytest.fixture
def site(tmp_path,monkeypatch):
    ctx=make_context(tmp_path,monkeypatch,publication=True)
    monkeypatch.setattr(reader,'_activation',lambda root:{'synthetic_verified_context':True})
    return ctx['source_root'],ctx['pages_root']
@pytest.fixture
def case(site,tmp_path):
    from bluefern_dispatches import gaza_correction_activation as activation
    source=site[0]/'review'/activation.EVIDENCE_PREFIX/'original'
    dest=tmp_path/'originals';dest.mkdir()
    for name in reader.ORIGINAL_BLOBS:shutil.copyfile(source/name,dest/('gaza-2026-10-25-'+name))
    return {'preimages_root':dest}
def memory(site):return dedupe.prior_memory_from_outputs(site[0],'gaza','2026-10-28')

import itertools
ROOT_STATES=list(itertools.product(('absent','original','corrected','partial','unknown'),repeat=3))
@pytest.mark.parametrize('states',ROOT_STATES)
@pytest.mark.parametrize('persisted',[False,True])
def test_replica_state_permutation_matrix(site,case,tmp_path,monkeypatch,states,persisted):
    template=tmp_path/'complete-profile-template';shutil.copytree(site[1],template)
    roots=('output/site','output/dispatches','bluefern-dispatches-pages')
    shutil.rmtree(site[1])
    for relative,state in zip(roots,states):
        base=site[0]/relative
        if state=='absent':continue
        if state=='original':
            directory=base/'gaza/editions/2026-10-25';directory.mkdir(parents=True)
            for name in release.rich.BLOBS:shutil.copyfile(case['preimages_root']/('gaza-2026-10-25-'+name),directory/name)
        else:
            shutil.copytree(template,base)
            p=base/'gaza/editions/2026-10-25/curation_manifest.json'
            if state=='partial':p.unlink();(base/reader.CORRECTION_PATH).unlink()
            elif state=='unknown':
                v=json.loads(p.read_bytes());v[0]['summary']='unreviewed';v[0].pop('schema_version');v[0].pop('corrected_presentation_profile');p.write_text(json.dumps(v));(base/reader.CORRECTION_PATH).unlink()
    memory_path=site[0]/'data/records/story_memory.json'
    if persisted:
        memory_path.parent.mkdir(parents=True,exist_ok=True)
        memory_path.write_text(json.dumps([{'dispatch_slug':'gaza','edition_date':'2026-10-25','story_id':sid,'title':'Archived old framing','summary':'Archived claim'} for sid in (release.rich.RETAIN,release.rich.WITHDRAW)]))
    before=memory_path.read_bytes() if persisted else None
    admissible='corrected' in states and not ({'partial','unknown'} & set(states))
    for order in itertools.permutations(roots):
        monkeypatch.setattr(reader,'REPLICA_ROOTS',order)
        captured=[]
        def classify(story,prior):captured.extend(prior);return 'new',[],None
        monkeypatch.setattr(dedupe,'classify_against_prior',classify)
        if admissible:
            rows,checked=memory(site)
            assert len(rows)==1 and checked==['2026-10-25']
            assert rows[0]['summary']==release.SUMMARY and rows[0]['latest_classification']==reader.ASSESSMENT_STATUS
            dedupe.dedupe_public_stories(site[0],'gaza','2026-10-28',[{'story_id':'new-fixture','title':'Distinct event','summary':'Distinct account'}],dry_run=True)
            assert len(captured)==1 and captured[0]['summary']==release.SUMMARY
            source_rows=list(reader.prior_source_records(site[0],gaza_sources._iter_prior_source_manifests(site[0],'2026-10-28',7),'2026-10-28'))
            assert len(source_rows)==1 and len(source_rows[0][1])==1 and 'title' not in source_rows[0][1][0]
        else:
            with pytest.raises(reader.CorrectedManifestError):memory(site)
            with pytest.raises(reader.CorrectedManifestError):dedupe.dedupe_public_stories(site[0],'gaza','2026-10-28',[],dry_run=True)
            with pytest.raises(reader.CorrectedManifestError):list(reader.prior_source_records(site[0],gaza_sources._iter_prior_source_manifests(site[0],'2026-10-28',7),'2026-10-28'))
        with pytest.raises(reader.CorrectedManifestError):reader.assert_generic_reconciliation_allowed(site[0],site[0]/'output/site',site[1])
        assert (memory_path.read_bytes() if memory_path.exists() else None)==before


@pytest.mark.parametrize('rootname',('output/site','output/dispatches','bluefern-dispatches-pages'))
@pytest.mark.parametrize('deleted',[combo for n in range(1,4) for combo in itertools.combinations(('curation','sources','record'),n)])
@pytest.mark.parametrize('healthy_other',[False,True])
def test_replica_deletion_combinations_are_not_hidden_by_healthy_copy(site,tmp_path,rootname,deleted,healthy_other):
    template=tmp_path/'deletion-template';shutil.copytree(site[1],template);shutil.rmtree(site[1]);target=site[0]/rootname;shutil.copytree(template,target)
    if healthy_other:
        other=next(p for p in ('output/site','output/dispatches','bluefern-dispatches-pages') if p!=rootname);shutil.copytree(template,site[0]/other)
    paths={'curation':'gaza/editions/2026-10-25/curation_manifest.json','sources':'gaza/editions/2026-10-25/sources_manifest.json','record':reader.CORRECTION_PATH}
    for key in deleted:(target/paths[key]).unlink()
    with pytest.raises(reader.CorrectedManifestError):memory(site)

@pytest.mark.parametrize('states',ROOT_STATES)
@pytest.mark.parametrize('persisted',[False,True])
def test_inactive_replica_matrix_never_approves_pending_bytes(site,case,tmp_path,monkeypatch,states,persisted):
    monkeypatch.setattr(reader,'_activation',lambda root:None)
    template=tmp_path/'inactive-template';shutil.copytree(site[1],template);shutil.rmtree(site[1])
    roots=('output/site','output/dispatches','bluefern-dispatches-pages')
    for relative,state in zip(roots,states):
        base=site[0]/relative
        if state=='absent':continue
        if state=='original':
            directory=base/'gaza/editions/2026-10-25';directory.mkdir(parents=True)
            for name in release.rich.BLOBS:shutil.copyfile(case['preimages_root']/('gaza-2026-10-25-'+name),directory/name)
        else:
            shutil.copytree(template,base)
            if state in ('partial','unknown'):
                (base/reader.CORRECTION_PATH).unlink()
                p=base/'gaza/editions/2026-10-25/curation_manifest.json'
                if state=='partial':p.unlink()
                else:
                    v=json.loads(p.read_bytes());v[0].pop('schema_version');v[0].pop('corrected_presentation_profile');p.write_text(json.dumps(v))
    memory_path=site[0]/'data/records/story_memory.json'
    if persisted:
        memory_path.parent.mkdir(parents=True,exist_ok=True);memory_path.write_text('[{"dispatch_slug":"gaza","edition_date":"2026-10-25","story_id":"legacy-memory","title":"Legacy"}]')
    before=memory_path.read_bytes() if persisted else None
    for order in itertools.permutations(roots):
        monkeypatch.setattr(reader,'REPLICA_ROOTS',order)
        if set(states)<={'absent','original'}:
            assert reader.corrected_profile_for_outputs(site[0],'2026-10-28') is None
            reader.assert_generic_reconciliation_allowed(site[0],site[0]/'output/site',site[1])
            rows,dates=memory(site)
            assert all(row['latest_classification']!=reader.ASSESSMENT_STATUS for row in rows)
            captured=[]
            def classify(story,prior):captured.extend(prior);return 'new',[],None
            monkeypatch.setattr(dedupe,'classify_against_prior',classify)
            dedupe.dedupe_public_stories(site[0],'gaza','2026-10-28',[{'story_id':'inactive-new','title':'Distinct event','summary':'Distinct account'}],dry_run=True)
            if persisted:assert any(row['story_id']=='legacy-memory' for row in captured)
        else:
            with pytest.raises(reader.CorrectedManifestError,match='pending'):memory(site)
            with pytest.raises(reader.CorrectedManifestError):reader.assert_generic_reconciliation_allowed(site[0],site[0]/'output/site',site[1])
        assert (memory_path.read_bytes() if memory_path.exists() else None)==before
