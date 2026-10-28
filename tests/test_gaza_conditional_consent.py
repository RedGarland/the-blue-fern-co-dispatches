"""Public authority is deliberately unconfigured; private consent stays private."""
import ast
import json
from pathlib import Path
import pytest
import test_gaza_oct25_release_package as pkg
import test_gaza_oct25_remote_plan as rem
from bluefern_dispatches import gaza_correction_activation as activation
activated=pkg.activated
context=pkg.context
case=rem.case


def test_public_source_distributes_no_private_trust_anchor():
    tree=ast.parse(Path(activation.__file__).read_text())
    assignments={node.target.id:node.value for node in tree.body if isinstance(node,ast.AnnAssign) and isinstance(node.target,ast.Name)}
    assert isinstance(assignments['REVIEW_PACKET_SHA'],ast.Constant)
    assert assignments['REVIEW_PACKET_SHA'].value is None
    assert not hasattr(activation,'CONSENT_SHA')
    assert not hasattr(activation,'CONDITIONAL_APPROVAL_SCHEMA')


@pytest.mark.parametrize('name',['REVIEW_PACKET_SHA','CONSENT_SHA','GAZA_CORRECTION_TRUST_ANCHOR','GAZA_REVIEW_PACKET_SHA'])
def test_environment_cannot_supply_authority(tmp_path,monkeypatch,name):
    monkeypatch.setattr(activation,'REVIEW_PACKET_SHA',None)
    monkeypatch.setenv(name,'a'*64)
    def forbidden(*args,**kwargs):
        raise AssertionError('must refuse before reading caller evidence')
    monkeypatch.setattr(activation,'_git',forbidden)
    with pytest.raises(activation.Error,match='formal authority unavailable'):
        activation.load_formal_proposal(tmp_path,'a'*40,'caller-proposal.json')


def test_self_consistent_caller_package_is_not_a_trust_provider(context,monkeypatch):
    monkeypatch.setattr(activation,'REVIEW_PACKET_SHA',None)
    with pytest.raises(activation.Error,match='formal authority unavailable'):
        pkg.make_plan(context)
    assert not context['output_root'].exists()


def test_committed_activation_cannot_fill_missing_trust_provider(activated,monkeypatch):
    monkeypatch.setattr(activation,'REVIEW_PACKET_SHA',None)
    with pytest.raises(activation.Error,match='formal authority unavailable'):
        activation.load_activation(activated)


def test_remote_plan_cannot_fill_missing_trust_provider(case,monkeypatch):
    monkeypatch.setattr(activation,'REVIEW_PACKET_SHA',None)
    with pytest.raises(activation.Error,match='formal authority unavailable'):
        rem.plan(case)
    assert not case['output_root'].exists()


@pytest.mark.parametrize('schema',['gaza_oct25_conditional_site_release_approval_v2','unknown',''])
def test_conditional_or_unknown_approval_is_unsupported_even_in_synthetic_trust_context(context,schema):
    root=context['source_root'];ap=context['approval_path'];a=json.loads((root/ap).read_bytes())
    pkg.fixtures.git(root,'checkout','--detach',context['proposal_commit'])
    a['schema_version']=schema
    a['conditional_consent']={'path':'fictional.json','sha256':'a'*64}
    pkg.fixtures.write(root,ap,a)
    context['approval_commit']=pkg.fixtures.commit(root,'SYNTHETIC unsupported conditional schema')
    with pytest.raises(activation.Error,match='unsupported approval schema'):
        pkg.make_plan(context)
    assert not context['output_root'].exists()
