"""Generate fictional evidence only in pytest tmp_path; never load private artifacts."""
import hashlib,json,subprocess
from datetime import datetime,timezone
from pathlib import Path
from bluefern_dispatches import gaza_corrected_manifest_reader as reader
from bluefern_dispatches import gaza_correction_activation as activation
from bluefern_dispatches import gaza_oct25_release_package as package

def encode(obj):return json.dumps(obj,sort_keys=True).encode()
def sha(raw):return hashlib.sha256(raw).hexdigest()
def blob(raw):return hashlib.sha1(b'blob '+str(len(raw)).encode()+b'\0'+raw).hexdigest()
def git(root,*args):return subprocess.run(['git','-C',str(root),*args],capture_output=True,check=True).stdout.decode().strip()
def init(root):
    root.mkdir(parents=True);git(root,'init');git(root,'config','user.name','Synthetic Test Author');git(root,'config','user.email','fixture@example.invalid')
def put(root,name,raw):
    p=root/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(raw);return sha(raw)
def commit(root):git(root,'add','.');git(root,'commit','-m','SYNTHETIC TEST ONLY');return git(root,'rev-parse','HEAD')

def make_context(tmp_path,monkeypatch,*,publication=False,parent_mutation=None):
    root=tmp_path/'source';init(root);pages=root/'bluefern-dispatches-pages';init(pages)
    put(root,'.gitignore',b'bluefern-dispatches-pages/\n');source_anchor=commit(root)
    monkeypatch.setattr(activation,'SOURCE_ANCHOR',source_anchor)
    story={'story_id':'synthetic-story','title':'Fictional original headline','summary':'Fictional original narrative.',
        'source_record_ids':['synthetic-source'],'source_ids':['synthetic-source'],'source_urls':['https://source.example.invalid/report'],
        'publisher_names':['Fictional Publisher']}
    original={name:('SYNTHETIC ORIGINAL '+name).encode() for name in reader.ORIGINAL_BLOBS}
    original['curation_manifest.json']=encode([story]);original['sources_manifest.json']=encode([{'source_record_id':'synthetic-source','url':story['source_urls'][0]}])
    prefix='gaza/editions/2026-10-25/'
    for name,raw in original.items():put(pages,prefix+name,raw)
    aggregates={p:b'<html><body>Synthetic original listing</body></html>' for p in ('gaza/index.html','gaza/archive.html')}
    aggregates['gaza/rss.xml']=b'<rss><channel><title>Synthetic original feed</title></channel></rss>'
    for name,raw in aggregates.items():put(pages,name,raw)
    for name in activation.edition.NO_AUDIO_AGGREGATE_PATHS:
        put(pages,name,b'[]' if name.endswith('.json') else b'<rss><channel/></rss>' if name.endswith('.xml') else b'<html><body/></html>')
    if parent_mutation=='parent_original':put(pages,prefix+'index.html',b'wrong original')
    if parent_mutation=='parent_correction':put(pages,reader.CORRECTION_PATH,b'preexisting')
    if parent_mutation in ('parent_correction_tree','parent_correction_link'):
        dest=pages/reader.CORRECTION_PATH;dest.parent.mkdir(parents=True,exist_ok=True)
        if parent_mutation=='parent_correction_tree':dest.mkdir();(dest/'child').write_bytes(b'preexisting')
        else:dest.symlink_to('missing-target')
    if parent_mutation and parent_mutation.startswith('parent_neighbor:'):
        target=pages/parent_mutation.split(':',1)[1];target.write_bytes(target.read_bytes()+b'new unrelated neighbor')
    base=commit(pages);monkeypatch.setattr(activation,'REVIEW_ANCHOR',base)
    monkeypatch.setattr(reader,'ORIGINAL_BLOBS',{name:blob(raw) for name,raw in original.items()})
    monkeypatch.setattr(activation,'PUBLICATION_AGGREGATE_PREIMAGES',{name:sha(raw) for name,raw in aggregates.items()})
    corrected={p:('SYNTHETIC CORRECTED '+p).encode() for p in activation.PUBLIC_HASHES}
    corrected[prefix+'curation_manifest.json']=encode([{**story,'title':'Fictional narrowed headline','summary':'Fictional narrowed narrative.','schema_version':reader.CURATION_SCHEMA,'corrected_presentation_profile':reader.CURATION_SCHEMA}])
    corrected[prefix+'sources_manifest.json']=encode([{'source_record_id':'synthetic-source','url':story['source_urls'][0],'projection_state':'synthetic_corrected'}])
    for name in ('gaza/index.html','gaza/archive.html'):
        corrected[name]=b'<html><body><ul><li><a href="editions/2026-10-25/">Fictional narrowed headline</a></li></ul></body></html>'
    corrected['gaza/rss.xml']=('<rss><channel><item><guid>https://dispatches.thebluefernco.com/gaza/editions/2026-10-25/</guid><title>Fictional narrowed headline</title></item><item><guid>'+activation.CORRECTION_ID+'</guid><title>Synthetic correction</title></item></channel></rss>').encode()
    monkeypatch.setattr(activation,'PUBLIC_HASHES',{p:sha(raw) for p,raw in corrected.items()})
    monkeypatch.setattr(reader,'PROFILE_HASHES',{p:sha(corrected[p]) for p in reader.PROFILE_HASHES})
    archived={activation.EVIDENCE_PREFIX+'original/'+name:raw for name,raw in original.items()}
    archived[activation.EVIDENCE_PREFIX+'current/excerpts.txt']=b'Fictional bounded supporting passage.'
    archived[activation.EVIDENCE_PREFIX+'current/provenance.json']=encode({'fictional':True,'source':'https://source.example.invalid/report'})
    archived.update({'public-preview/'+p:raw for p,raw in corrected.items()})
    receipt={'schema_version':'gaza_oct25_legacy_release_review_packet_v1','already_published':False,'full_site_ready':False,'artifact_hashes':{p:sha(raw) for p,raw in archived.items()}}
    for name,raw in archived.items():put(root,'review/'+name,raw)
    digest=put(root,'review/packet.json',encode(receipt));monkeypatch.setattr(activation,'REVIEW_PACKET_SHA',digest)
    lineage={'original_artifacts':{n:sha(raw) for n,raw in original.items()},'story_source_ids':{story['story_id']:story['source_record_ids']},'current_provenance_sha256':sha(archived[activation.EVIDENCE_PREFIX+'current/provenance.json']),'current_excerpt_sha256':sha(archived[activation.EVIDENCE_PREFIX+'current/excerpts.txt'])}
    bindings={}
    for kind in ('editorial_decision','independent_audit','source_lineage'):
        evidence=dict(schema_version='gaza_oct25_formal_evidence_v1',kind=kind,profile=reader.PROFILE,review_packet_sha256=digest,decision='approved',reviewer='Synthetic '+kind,reviewed_at='2026-10-28T03:00:00Z',public_artifacts=activation.PUBLIC_HASHES)
        if kind=='source_lineage':evidence['lineage']=lineage
        path='evidence/'+kind+'.json';bindings[kind]={'path':path,'sha256':put(root,path,encode(evidence))}
    proposal=dict(schema_version=activation.PROPOSAL_SCHEMA,state='formally_reviewed',profile=reader.PROFILE,owning_edition_date='2026-10-25',pages_head=base,public_artifacts=activation.PUBLIC_HASHES,review_packet_path='review/packet.json',review_packet_sha256=digest,formal_evidence=bindings,execution_local_date='2026-10-27',preparer='Synthetic Preparer',publication_authorized=False,audio_authorized=False,social_authorized=False)
    psha=put(root,'proposal.json',encode(proposal));pc=commit(root)
    approval=dict(schema_version=activation.APPROVAL_SCHEMA,profile=reader.PROFILE,proposal_commit=pc,proposal_path='proposal.json',proposal_sha256=psha,public_artifacts=activation.PUBLIC_HASHES,execution_local_date='2026-10-27',approver='Synthetic Approver',approved_at='2026-10-28T03:01:00Z',package_authorized=True,editorial_authorized=True,publication_authorized=publication,audio_authorized=False,social_authorized=False)
    ap='approvals/gaza/synthetic.json';put(root,ap,encode(approval));ac=commit(root)
    if publication:
        dest=pages/reader.CORRECTION_PATH
        if parent_mutation=='parent_correction_tree':
            import shutil
            shutil.rmtree(dest)
        if parent_mutation=='parent_correction_link':dest.unlink()
        for name,raw in corrected.items():put(pages,name,raw)
        published=commit(pages)
        put(root,activation.REGISTRY_PATH,encode(dict(schema_version=activation.REGISTRY_SCHEMA,state='active',profile=reader.PROFILE,proposal_commit=pc,proposal_path='proposal.json',approval_commit=ac,approval_path=ap,published_pages_commit=published)));commit(root)
    monkeypatch.setattr(package,'_now',lambda:datetime(2026,10,28,3,2,tzinfo=timezone.utc))
    return {'source_root':root,'pages_root':pages,'proposal_commit':pc,'proposal_path':'proposal.json','approval_commit':ac,'approval_path':ap}
