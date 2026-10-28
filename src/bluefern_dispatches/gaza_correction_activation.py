"""Read-only committed activation binding. Never creates approvals/registries.

An unpublished review profile does not activate itself. Normal fresh workspaces
remain inactive; deleting a tracked registry is drift, not deactivation.
"""
from __future__ import annotations
import hashlib,json,re,subprocess
from pathlib import Path
from datetime import datetime, date, timezone
from xml.etree import ElementTree
from . import gaza_correction_proofs as edition
from . import gaza_historical_correction as legacy
from . import gaza_corrected_manifest_reader as reader
Error=reader.CorrectedManifestError
REGISTRY_PATH='data/records/gaza_oct25_correction_activation.json'
REGISTRY_SCHEMA='gaza_oct25_correction_activation_v1'
PUBLIC_REGISTRY_SCHEMA='gaza_public_correction_activation_v1'
PUBLISHED_COMMIT='91dbf49bfe69dca65c330b7ea1cba16a6914d860'
PUBLISHED_TREE='485a7c186b601805d222a036f8e1bb5e9eda6a49'
PUBLISHED_PARENT='7cd2baf2e3e2a7ced255730faccc41d89c8068b0'
PROPOSAL_SCHEMA='gaza_oct25_formal_site_release_proposal_v1'
APPROVAL_SCHEMA='gaza_oct25_site_release_approval_v1'
SOURCE_ANCHOR='b1c9807f4df784d3fde744d2f6680e6a9175a58f'
REVIEW_ANCHOR='5f8355457cc149898b09d399e06705797ce059e3'
PUBLIC_HASHES={'gaza/archive.html': 'c47a89a865da672d5d8800d876f3d6eb3755fee0ff7b808345d3fb9259fc648d', 'gaza/corrections/gaza-scope-correction-v1-2adf5ce5e94aa0a828f4/correction.json': '799041cb081a669ba36d8a8235021a7fab97cb7d30f4d88aab76eed2414f1532', 'gaza/corrections/gaza-scope-correction-v1-2adf5ce5e94aa0a828f4/index.html': '08c4a1ccf9d791b0b8f048c4aae1def2e3246d5ebc7ea823ab4817e8ebc2cb86', 'gaza/editions/2026-10-25/curation_manifest.json': 'c4a7e51ff04bcc07974b4ddf2f095d3e73edd1efbb228e29d119bc250059839f', 'gaza/editions/2026-10-25/dedupe_report.json': '3cc89a919d5d202301ce25aaa382360a7df1718cc051cec8e6278552e4dde504', 'gaza/editions/2026-10-25/edition_manifest.json': '4f500eddde6e6b7d2a5c0ed83f01668b0104573251124432a939ad751ad3a8bf', 'gaza/editions/2026-10-25/index.html': '80ce65cf4c5e91543f86537ea90348dcb68ba44aa23635d5ca5503364c281a9b', 'gaza/editions/2026-10-25/source_quality_report.md': 'f67861d9f9e8586c1ed90b3c2c1c8c646d328273c2b72506b341f7bbcba4f4a8', 'gaza/editions/2026-10-25/sources_manifest.json': 'f77026c8ec1a5e81d86a7bd07b443b6a935e5fad410685dce08aad400cae7cd8', 'gaza/index.html': '5a68bd597a9c6fecedee7af228c97b77549c0e790d67afe0b92be012559abba3', 'gaza/rss.xml': 'ae33ffd43a7da09bab4c8d3e8dc29674caeda45c5ed29f6edb86ac6777bdf838'}
# No private review or consent anchor is distributed in public source.
# This is deliberately not configurable by caller, CLI, file, or environment.
# Synthetic tests patch it only inside their isolated process.
REVIEW_PACKET_SHA: str | None = None


def _git(root:Path,*args:str)->bytes:
    p=subprocess.run(['git','-C',str(root),*args],capture_output=True)
    if p.returncode:raise Error('activation Git proof failed: '+str(args[0]))
    return p.stdout


def _oid(value):
    if not isinstance(value,str) or not re.fullmatch('[0-9a-f]{40}',value):raise Error('activation requires exact commit OID')
    return value


def _path(value):
    if not isinstance(value,str) or not value or value.startswith('/') or '\\' in value or ':' in value or any(x in ('','..','.') for x in value.split('/')):
        raise Error('unsafe activation evidence path')
    return value


def _blob(root:Path,commit:str,path:str)->bytes:
    _oid(commit);_path(path)
    rows=_git(root,'ls-tree','-z',commit,'--',path).split(b'\0');rows=[x for x in rows if x]
    if len(rows)!=1:raise Error('activation evidence missing from committed tree')
    metadata,name=rows[0].split(b'\t',1);mode,kind,oid=metadata.split()
    if mode not in (b'100644',b'100755') or kind!=b'blob' or name.decode()!=path:raise Error('activation evidence is not a regular committed file')
    return _git(root,'cat-file','blob',oid.decode())


def _json(raw):
    def pairs(values):
        obj={}
        for key,value in values:
            if key in obj:raise Error('duplicate activation JSON key')
            obj[key]=value
        return obj
    try:value=json.loads(raw,object_pairs_hook=pairs)
    except (ValueError,UnicodeError) as exc:raise Error('invalid activation JSON') from exc
    if not isinstance(value,dict):raise Error('activation evidence must be an object')
    return value


def _exact(obj,keys,label):
    if set(obj)!=set(keys):raise Error(label+' fields differ')


def _sha(raw):return hashlib.sha256(raw).hexdigest()


EVIDENCE_PREFIX='evidence/pending-site-packet/evidence/editorial-proposal/evidence/'
DATE='2026-10-25'
CORRECTION_ID='gaza-scope-correction-v1-2adf5ce5e94aa0a828f4'

def _identity(value):
    if not isinstance(value,str) or not value.strip():raise Error('identity must be a nonempty string')
    return value.strip().casefold()

def _time(value):
    try:
        if not isinstance(value,str):raise ValueError()
        result=datetime.fromisoformat(value.replace('Z','+00:00'))
        if result.tzinfo is None:raise ValueError()
        return result
    except ValueError as exc:raise Error('timezone-aware evidence timestamp required') from exc

def _date(value):
    try:
        if not isinstance(value,str) or date.fromisoformat(value).isoformat()!=value:raise ValueError()
    except ValueError as exc:raise Error('exact execution local date required') from exc

def _lineage_expected(source,commit,prefix,review):
    originals={name:review['artifact_hashes'][EVIDENCE_PREFIX+'original/'+name] for name in
        ('index.html','edition_manifest.json','curation_manifest.json','sources_manifest.json','dedupe_report.json','source_quality_report.md')}
    stories=json.loads(_blob(source,commit,prefix+'/'+EVIDENCE_PREFIX+'original/curation_manifest.json'))
    return {'original_artifacts':originals,
        'story_source_ids':{s['story_id']:s['source_record_ids'] for s in stories},
        'current_provenance_sha256':review['artifact_hashes'][EVIDENCE_PREFIX+'current/provenance.json'],
        'current_excerpt_sha256':review['artifact_hashes'][EVIDENCE_PREFIX+'current/excerpts.txt']}

def _owned_aggregate(raw,path):
    text=raw.decode('utf-8')
    if path.endswith('.xml'):
        ElementTree.fromstring(raw)
        nodes=legacy._StrictHtmlTreeParser(text,void_tags=set()).finish()
        identities=('https://dispatches.thebluefernco.com/gaza/editions/'+DATE+'/',CORRECTION_ID)
        selected=[]
        for identity in identities:
            owners=[n for n in nodes if n.tag=='item' and any(c.tag=='guid' and edition._within(c,n) and edition._text(c)==identity for c in nodes)]
            owner=edition._one(owners,'unique activated RSS identity')
            selected.append(text[owner.start:owner.end])
        return selected
    nodes=legacy._StrictHtmlTreeParser(text).finish();owners=[]
    for link in nodes:
        if link.tag!='a' or link.attrs.get('href')!='editions/'+DATE+'/':continue
        node=link.parent
        while node is not None and node.tag!='li':node=node.parent
        if node is None:raise Error('activated aggregate owner missing')
        if node not in owners:owners.append(node)
    owner=edition._one(owners,'unique activated Oct25 listing')
    return [text[owner.start:owner.end]]

def _audio_absence(pages,revision):
    try:
        edition._validate_absent_audio_profile(pages,DATE,revision)
        # The shared validator examines current aggregates; additionally bind the
        # historical/current committed aggregate representations independently.
        for path in edition.NO_AUDIO_AGGREGATE_PATHS:
            raw=_blob(pages,revision,path)
            if any(DATE in v for v in edition._decoded_aggregate_variants(raw.decode('utf-8-sig'),Path(path))):
                raise Error('committed audio aggregate references corrected edition')
    except (edition.Error, ValueError, UnicodeError, OSError) as exc:
        raise Error('activation no-audio evidence is invalid') from exc


def load_formal_proposal(source:Path,commit:str,path:str):
    if REVIEW_PACKET_SHA is None:
        raise Error('formal authority unavailable: private trust-provider integration is deferred')
    _oid(commit);_path(path)
    _git(source,'merge-base','--is-ancestor',SOURCE_ANCHOR,commit)
    raw=_blob(source,commit,path);p=_json(raw)
    _exact(p,('schema_version','state','profile','owning_edition_date','pages_head','public_artifacts','review_packet_path','review_packet_sha256','formal_evidence','execution_local_date','preparer','publication_authorized','audio_authorized','social_authorized'),'formal proposal')
    if p['schema_version']!=PROPOSAL_SCHEMA or p['state']!='formally_reviewed' or p['profile']!=reader.PROFILE or p['owning_edition_date']!='2026-10-25':raise Error('unsupported formal correction proposal')
    if p['public_artifacts']!=PUBLIC_HASHES or p['review_packet_sha256']!=REVIEW_PACKET_SHA:raise Error('formal proposal differs from exact reviewed materialization')
    if any(p[x] is not False for x in ('publication_authorized','audio_authorized','social_authorized')):raise Error('formal proposal grants unauthorized action')
    _oid(p['pages_head']);_identity(p['preparer']);_date(p['execution_local_date'])
    review_raw=_blob(source,commit,p['review_packet_path'])
    if _sha(review_raw)!=REVIEW_PACKET_SHA:raise Error('committed review packet differs')
    review=_json(review_raw);prefix=str(Path(p['review_packet_path']).parent)
    if review.get('schema_version')!='gaza_oct25_legacy_release_review_packet_v1' or review.get('already_published') is not False or review.get('full_site_ready') is not False:raise Error('review evidence is not the exact pending profile')
    expected_files={p['review_packet_path']}
    for relative,digest in review['artifact_hashes'].items():
        target=prefix+'/'+_path(relative);expected_files.add(target)
        if _sha(_blob(source,commit,target))!=digest:raise Error('committed review evidence artifact drift')
    actual=set(_git(source,'ls-tree','-r','--name-only',commit,'--',prefix).decode().splitlines())
    if actual!=expected_files:raise Error('committed review exact inventory differs')
    if set(p['formal_evidence'])!={'editorial_decision','independent_audit','source_lineage'}:raise Error('formal decision/audit/lineage missing')
    reviewers={}
    for kind,binding in p['formal_evidence'].items():
        _exact(binding,('path','sha256'),'formal evidence binding')
        evidence_raw=_blob(source,commit,binding['path'])
        if _sha(evidence_raw)!=binding['sha256']:raise Error('formal evidence hash differs')
        e=_json(evidence_raw)
        keys={'schema_version','kind','profile','review_packet_sha256','decision','reviewer','reviewed_at','public_artifacts'}
        if kind=='source_lineage':keys.add('lineage')
        _exact(e,keys,'formal evidence')
        if kind=='source_lineage' and legacy._canonical_bytes(e['lineage'])!=legacy._canonical_bytes(_lineage_expected(source,commit,prefix,review)):
            raise Error('source lineage differs from exact original and current evidence')
        if e['schema_version']!='gaza_oct25_formal_evidence_v1' or e['kind']!=kind or e['profile']!=reader.PROFILE or e['review_packet_sha256']!=REVIEW_PACKET_SHA or e['public_artifacts']!=PUBLIC_HASHES or e['decision']!='approved':raise Error('formal evidence does not approve exact profile')
        reviewers[kind]=_identity(e['reviewer']);_time(e['reviewed_at'])
    if reviewers['independent_audit'] in {_identity(p['preparer']),reviewers['editorial_decision']}:
        raise Error('independent audit cannot be self-approved')
    return p,raw


def load_approval(source:Path,proposal_commit:str,proposal_path:str,approval_commit:str,approval_path:str,*,publication:bool):
    _oid(proposal_commit);_oid(approval_commit);_path(proposal_path);_path(approval_path)
    if type(publication) is not bool:raise Error('publication scope must be boolean')
    p,praw=load_formal_proposal(source,proposal_commit,proposal_path)
    parents=_git(source,'rev-list','--parents','-n','1',approval_commit).decode().split()[1:]
    if parents!=[proposal_commit]:raise Error('approval must be a direct approval-only child')
    changed=_git(source,'diff-tree','--no-commit-id','--name-only','-r',proposal_commit,approval_commit).decode().splitlines()
    if changed!=[approval_path] or not approval_path.startswith('approvals/gaza/'):raise Error('approval commit contains other changes')
    raw=_blob(source,approval_commit,approval_path);a=_json(raw)
    if a.get('schema_version') != APPROVAL_SCHEMA:
        raise Error('unsupported approval schema: conditional authority is deferred')
    keys={'schema_version','profile','proposal_commit','proposal_path','proposal_sha256','public_artifacts','execution_local_date','approver','approved_at','package_authorized','editorial_authorized','publication_authorized','audio_authorized','social_authorized'}
    _exact(a,keys,'approval')
    if a['schema_version']!=APPROVAL_SCHEMA or a['profile']!=reader.PROFILE or a['proposal_commit']!=proposal_commit or a['proposal_path']!=proposal_path or a['proposal_sha256']!=_sha(praw) or a['public_artifacts']!=PUBLIC_HASHES or a['execution_local_date']!=p['execution_local_date']:raise Error('approval does not bind exact proposal')
    if a['package_authorized'] is not True or a['editorial_authorized'] is not True or a['publication_authorized'] is not publication or a['audio_authorized'] is not False or a['social_authorized'] is not False:raise Error('approval scope differs')
    _identity(a['approver']);approved=_time(a['approved_at'])
    for binding in p['formal_evidence'].values():
        evidence=_json(_blob(source,proposal_commit,binding['path']))
        if _time(evidence['reviewed_at'])>approved:raise Error('approval predates formal review')
    return p,a


PUBLICATION_AGGREGATE_PREIMAGES={'gaza/index.html': 'b85e44c651be65d3612c5ec231f2c199dc867b7b4a22ffe5aac27892416838dc', 'gaza/archive.html': 'c1ca2cc0731e4943efd312a39e83d1291935512f4a98542dede173fa6a6a568f', 'gaza/rss.xml': '38be5b112268e9c32ef48fe1929d1fa835f8e4cd8597824678d8d26054d10571'}


def _publication_preimages(pages,parent):
    # Fixed postimages may only replace the exact reviewed aggregate preimages.
    # Ongoing runtime validation still permits unrelated later additions.
    for path,expected in PUBLICATION_AGGREGATE_PREIMAGES.items():
        if _sha(_blob(pages,parent,path))!=expected:
            raise Error('publication aggregate preimage differs; newer neighbors require a new reviewed materialization')
    for name,expected in reader.ORIGINAL_BLOBS.items():
        raw=_blob(pages,parent,'gaza/editions/'+DATE+'/'+name)
        oid=hashlib.sha1(b'blob '+str(len(raw)).encode()+b'\0'+raw).hexdigest()
        if oid!=expected:raise Error('published parent original edition preimage differs')
    for path in PUBLIC_HASHES:
        if not path.startswith('gaza/corrections/'):continue
        if _git(pages,'ls-tree','-z',parent,'--',path):raise Error('correction destination already exists in parent')
        for ancestor in Path(path).parents:
            if ancestor==Path('.'):continue
            entry=_git(pages,'ls-tree','-z',parent,'--',ancestor.as_posix())
            if entry and not entry.startswith(b'040000 tree '):raise Error('correction parent ancestor is not a tree')


def load_activation(root:Path):
    root=Path(root).absolute();local=root/REGISTRY_PATH
    if not (root/'.git').exists():
        if local.exists() or local.is_symlink():raise Error('uncommitted correction registry cannot activate')
        return None
    head=_git(root,'rev-parse','HEAD').decode().strip()
    exists=bool(_git(root,'ls-tree','-z',head,'--',REGISTRY_PATH))
    if not exists:
        if _git(root,'log','--format=%H','-1',head,'--',REGISTRY_PATH).strip():
            raise Error('tracked activation registry deletion is not deactivation')
        if local.exists() or local.is_symlink():raise Error('uncommitted correction registry cannot activate')
        return None
    raw=_blob(root,head,REGISTRY_PATH)
    if reader._read_regular(root,REGISTRY_PATH)!=raw:raise Error('working correction registry differs from committed state')
    registry=_json(raw)
    if registry.get('schema_version') == PUBLIC_REGISTRY_SCHEMA:
        return _load_public_activation(root,head,raw,registry)
    _exact(registry,('schema_version','state','profile','proposal_commit','proposal_path','approval_commit','approval_path','published_pages_commit'),'activation registry')
    if registry['schema_version']!=REGISTRY_SCHEMA or registry['state']!='active' or registry['profile']!=reader.PROFILE:raise Error('unknown correction activation')
    pc=_oid(registry['proposal_commit']);ac=_oid(registry['approval_commit']);published=_oid(registry['published_pages_commit'])
    p,a=load_approval(root,pc,registry['proposal_path'],ac,registry['approval_path'],publication=True)
    now=datetime.now(timezone.utc)
    if _time(a['approved_at'])>now:raise Error('activation consent is in the future')
    for binding in p['formal_evidence'].values():
        evidence=_json(_blob(root,pc,binding['path']))
        if _time(evidence['reviewed_at'])>now:raise Error('activation technical review is in the future')
    _git(root,'merge-base','--is-ancestor',ac,head)
    pages=root/'bluefern-dispatches-pages'
    parents=_git(pages,'rev-list','--parents','-n','1',published).decode().split()[1:]
    if parents!=[p['pages_head']]:raise Error('published correction parent differs')
    _git(pages,'merge-base','--is-ancestor',REVIEW_ANCHOR,p['pages_head'])
    _publication_preimages(pages,p['pages_head'])
    changed=set(_git(pages,'diff-tree','--no-commit-id','--name-only','-r',p['pages_head'],published).decode().splitlines())
    if changed!=set(PUBLIC_HASHES):raise Error('published correction path set differs')
    for path,digest in PUBLIC_HASHES.items():
        if _sha(_blob(pages,published,path))!=digest:raise Error('published correction bytes differ from approval')
    current=_git(pages,'rev-parse','HEAD').decode().strip();_git(pages,'merge-base','--is-ancestor',published,current)
    _audio_absence(pages,published);_audio_absence(pages,current)
    for path in ('gaza/index.html','gaza/archive.html','gaza/rss.xml'):
        approved=_owned_aggregate(_blob(pages,published,path),path)
        try:
            if _owned_aggregate(_blob(pages,current,path),path)!=approved or _owned_aggregate(reader._read_regular(pages,path),path)!=approved:
                raise Error('current owning Oct25 aggregate projection drifted')
        except (edition.Error, ValueError, UnicodeError, ElementTree.ParseError) as exc:
            raise Error('current owning Oct25 aggregate projection invalid') from exc
    for path,digest in reader.PROFILE_HASHES.items():
        if _sha(_blob(pages,current,path))!=digest or _sha(reader._read_regular(pages,path))!=digest:raise Error('current corrected Pages profile drifted')
    return {'profile':reader.PROFILE,'registry_source_head':head,'registry_sha256':_sha(raw),'approval_commit':ac,'published_pages_commit':published,'current_pages_head':current}


def _load_public_activation(root,head,raw,registry):
    """Protect an already public correction; never establish release authority."""
    _exact(registry,('schema_version','state','profile','published_pages_commit'),'public activation registry')
    if registry['state'] != 'active' or registry['profile'] != reader.PROFILE or registry['published_pages_commit'] != PUBLISHED_COMMIT:
        raise Error('public activation differs from fixed published history')
    pages=root/'bluefern-dispatches-pages'
    if _git(pages,'rev-parse',PUBLISHED_COMMIT+'^{tree}').decode().strip() != PUBLISHED_TREE:
        raise Error('public correction tree differs')
    parents=_git(pages,'rev-list','--parents','-n','1',PUBLISHED_COMMIT).decode().split()[1:]
    if parents != [PUBLISHED_PARENT]:raise Error('public correction parent differs')
    _publication_preimages(pages,PUBLISHED_PARENT)
    changed=set(_git(pages,'diff-tree','--no-commit-id','--name-only','-r',PUBLISHED_PARENT,PUBLISHED_COMMIT).decode().splitlines())
    if changed != set(PUBLIC_HASHES):raise Error('public correction path set differs')
    for path,digest in PUBLIC_HASHES.items():
        if _sha(_blob(pages,PUBLISHED_COMMIT,path)) != digest:raise Error('public correction bytes differ')
    current=_git(pages,'rev-parse','HEAD').decode().strip()
    _git(pages,'merge-base','--is-ancestor',PUBLISHED_COMMIT,current)
    _audio_absence(pages,PUBLISHED_COMMIT);_audio_absence(pages,current)
    for path in ('gaza/index.html','gaza/archive.html','gaza/rss.xml'):
        expected=_owned_aggregate(_blob(pages,PUBLISHED_COMMIT,path),path)
        try:
            if _owned_aggregate(_blob(pages,current,path),path) != expected or _owned_aggregate(reader._read_regular(pages,path),path) != expected:
                raise Error('current owning Oct25 aggregate projection drifted')
        except (edition.Error,ValueError,UnicodeError,ElementTree.ParseError) as exc:
            raise Error('current owning Oct25 aggregate projection invalid') from exc
    for path,digest in reader.PROFILE_HASHES.items():
        if _sha(_blob(pages,current,path)) != digest or _sha(reader._read_regular(pages,path)) != digest:
            raise Error('current corrected Pages profile drifted')
    return {'profile':reader.PROFILE,'registry_source_head':head,'registry_sha256':_sha(raw),
        'published_pages_commit':PUBLISHED_COMMIT,'current_pages_head':current,
        'authority_basis':'fixed_public_history','publication_authorized':False}
