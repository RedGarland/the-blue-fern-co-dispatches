"""Fictional exact-profile inputs; no private snapshots or source records."""
import json,shutil
from pathlib import Path
import pytest
from bluefern_dispatches import gaza_corrected_manifest_reader as reader,story_dedupe as dedupe,gaza_audio,bluesky_post,generator
from test_gaza_correction_fixture_support import make_context
@pytest.fixture
def site(tmp_path,monkeypatch):
    context=make_context(tmp_path,monkeypatch,publication=True)
    monkeypatch.setattr(reader,'_activation',lambda root:{'synthetic_verified_context':True})
    return context['source_root'],context['pages_root']
def memory(site):return dedupe.prior_memory_from_outputs(site[0],'gaza','2026-10-28')
@pytest.mark.parametrize('relative',list(reader.PROFILE_HASHES))
def test_every_bound_artifact_is_immutable(site,relative):
    p=site[1]/relative;p.write_bytes(p.read_bytes()+b' ')
    with pytest.raises(reader.CorrectedManifestError):memory(site)
@pytest.mark.parametrize('key,value',[('score',99),('materially_concerns_gaza',False),('dedupe_classification','new'),('summary','Unreviewed'),('schema_version','unknown'),('source_record_ids',['foreign']),('extra',True)])
def test_no_invented_or_rehashed_fields(site,key,value):
    p=site[1]/'gaza/editions/2026-10-25/curation_manifest.json';rows=json.loads(p.read_bytes());rows[0][key]=value;p.write_text(json.dumps(rows))
    with pytest.raises(reader.CorrectedManifestError):memory(site)
@pytest.mark.parametrize('kind',['missing','dangling','file_link','parent_link'])
def test_bound_paths_reject_missing_or_redirected_components(site,tmp_path,kind):
    p=site[1]/reader.CORRECTION_PATH
    if kind=='missing':p.unlink()
    elif kind=='dangling':p.unlink();p.symlink_to('missing')
    elif kind=='file_link':q=tmp_path/'other';shutil.copyfile(p,q);p.unlink();p.symlink_to(q)
    else:q=tmp_path/'other';shutil.move(str(p.parent),q);p.parent.symlink_to(q,target_is_directory=True)
    with pytest.raises(reader.CorrectedManifestError):memory(site)
@pytest.mark.parametrize('kind',['intact','missing_record','stripped_markers','all_absent'])
def test_site_only_profile_never_creates_audio_or_social_authority(site,kind):
    if kind=='missing_record':(site[1]/reader.CORRECTION_PATH).unlink()
    elif kind=='stripped_markers':
        p=site[1]/'gaza/editions/2026-10-25/curation_manifest.json';rows=json.loads(p.read_bytes());rows[0].pop('corrected_presentation_profile');p.write_text(json.dumps(rows))
    elif kind=='all_absent':shutil.rmtree(site[1])
    with pytest.raises(reader.CorrectedManifestError):gaza_audio.load_gaza_audio_inputs(site[0],'2026-10-25')
    result=bluesky_post.maybe_post_gaza_dispatch_to_bluesky(edition_date='2026-10-25',public_url='https://example.invalid',run_succeeded=True,post_requested=True,project_root=site[0],force_post=True)
    assert result['status']=='blocked' and result['post_uri'] is None

def test_inactive_ordinary_legacy_story_is_not_blocked(tmp_path):
    p=tmp_path/'output/site/gaza/editions/2026-10-25/curation_manifest.json';p.parent.mkdir(parents=True)
    p.write_text('[{"story_id":"ordinary","title":"Fictional legacy story","included_in_public_summary":true}]')
    rows,dates=dedupe.prior_memory_from_outputs(tmp_path,'gaza','2026-10-28')
    assert len(rows)==1 and rows[0]['story_id']=='ordinary' and rows[0]['latest_classification']=='new'

def test_generic_reconciliation_refuses_before_any_writes(site,tmp_path):
    target=site[0]/'output/site';before={p.relative_to(site[0]):p.read_bytes() for p in site[0].rglob('*') if p.is_file()}
    with pytest.raises(reader.CorrectedManifestError):generator.reconcile_gaza_public_editions(site[0],target,False,[],pages_repo=site[1],backup_root=tmp_path/'backup')
    assert before=={p.relative_to(site[0]):p.read_bytes() for p in site[0].rglob('*') if p.is_file()}
    assert not (tmp_path/'backup').exists()
