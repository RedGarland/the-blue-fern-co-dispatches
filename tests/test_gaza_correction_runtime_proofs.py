import sys
from pathlib import Path
import pytest
from bluefern_dispatches import gaza_correction_proofs as proofs
from bluefern_dispatches import gaza_correction_activation as activation
from bluefern_dispatches import gaza_oct25_release_package as package
from bluefern_dispatches import gaza_corrected_manifest_reader as reader
from test_gaza_correction_fixture_support import make_context

def test_no_unpublished_preparation_dependency():
    import subprocess,os
    code="from bluefern_dispatches import gaza_correction_activation,gaza_oct25_release_package; import sys; assert not any('bluefern_dispatches.'+n in sys.modules for n in ('gaza_edition_correction','gaza_presentation_correction','gaza_oct25_presentation','gaza_oct25_site_preview'))"
    subprocess.run([sys.executable,'-c',code],check=True,env=os.environ.copy())


def test_generic_synthetic_package_without_actual_artifacts(tmp_path,monkeypatch):
    context=make_context(tmp_path,monkeypatch)
    plan=package.plan(**context)
    result=package.stage(plan=plan,source_root=context['source_root'],pages_root=context['pages_root'],output_root=tmp_path/'private-staging')
    assert package.verify(plan=plan,source_root=context['source_root'],pages_root=context['pages_root'],package_root=Path(result['package_path']))['status']=='verified_private_release_package'

def test_generic_synthetic_activation_without_actual_artifacts(tmp_path,monkeypatch):
    context=make_context(tmp_path,monkeypatch,publication=True)
    assert activation.load_activation(context['source_root'])['profile']==reader.PROFILE
    assert reader.corrected_profile_for_outputs(context['source_root'],'2026-10-28') is not None

@pytest.mark.parametrize('name,text',[('feed.xml','<rss><link>2026&#45;10&#45;25</link></rss>'),('feed.json','{"url":"2026\\u002d10\\u002d25"}'),('index.html','<html><a href="2026%2D10%2D25">link</a></html>')])
def test_extracted_decoder_retains_escaped_reference_detection(name,text):
    assert any('2026-10-25' in v for v in proofs._decoded_aggregate_variants(text,Path(name)))

@pytest.mark.parametrize('kind',['symlink','extra','directory'])
def test_extracted_inventory_retains_failclosed_shape(tmp_path,kind):
    root=tmp_path/'packet';root.mkdir();(root/'bound').write_bytes(b'bound')
    if kind=='symlink':(root/'link').symlink_to('missing')
    elif kind=='extra':(root/'extra').write_bytes(b'extra')
    else:(root/'extra').mkdir()
    with pytest.raises(proofs.Error):proofs._inventory(root,{'bound':b'bound'})
