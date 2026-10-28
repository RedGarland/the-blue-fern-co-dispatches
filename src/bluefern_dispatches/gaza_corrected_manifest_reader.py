"""Read one exact corrected Oct25 profile; never grants release authority.

Digest constants bind the reviewed content, not caller-supplied self hashes.
Any future corrected profile/date materialization needs explicit code review.
"""
from __future__ import annotations
import hashlib
import json
import stat
from pathlib import Path

class CorrectedManifestError(ValueError):
    pass

CURATION_SCHEMA = 'gaza_corrected_story_record_v1'
PROFILE = 'gaza_oct25_legacy_corrected_profile_v1'
CORRECTION_PATH = 'gaza/corrections/gaza-scope-correction-v1-2adf5ce5e94aa0a828f4/correction.json'
SOURCES_SCHEMA = 'gaza_corrected_source_links_v1'
ASSESSMENT_STATUS = 'historical_assessment_not_recomputed'
PROFILE_HASHES = {'gaza/corrections/gaza-scope-correction-v1-2adf5ce5e94aa0a828f4/correction.json': '799041cb081a669ba36d8a8235021a7fab97cb7d30f4d88aab76eed2414f1532', 'gaza/corrections/gaza-scope-correction-v1-2adf5ce5e94aa0a828f4/index.html': '08c4a1ccf9d791b0b8f048c4aae1def2e3246d5ebc7ea823ab4817e8ebc2cb86', 'gaza/editions/2026-10-25/curation_manifest.json': 'c4a7e51ff04bcc07974b4ddf2f095d3e73edd1efbb228e29d119bc250059839f', 'gaza/editions/2026-10-25/dedupe_report.json': '3cc89a919d5d202301ce25aaa382360a7df1718cc051cec8e6278552e4dde504', 'gaza/editions/2026-10-25/edition_manifest.json': '4f500eddde6e6b7d2a5c0ed83f01668b0104573251124432a939ad751ad3a8bf', 'gaza/editions/2026-10-25/index.html': '80ce65cf4c5e91543f86537ea90348dcb68ba44aa23635d5ca5503364c281a9b', 'gaza/editions/2026-10-25/source_quality_report.md': 'f67861d9f9e8586c1ed90b3c2c1c8c646d328273c2b72506b341f7bbcba4f4a8', 'gaza/editions/2026-10-25/sources_manifest.json': 'f77026c8ec1a5e81d86a7bd07b443b6a935e5fad410685dce08aad400cae7cd8'}


def _read_regular(root: Path, relative: str) -> bytes:
    path = root
    # Check every ancestor, including an ignored/dangling symlink or Windows
    # reparse point. No path resolution that hides redirected components.
    for component in reversed(root.absolute().parents):
        _check(component, directory=True)
    _check(root, directory=True)
    parts = Path(relative).parts
    if Path(relative).is_absolute() or any(p in ('..', '.') for p in parts):
        raise CorrectedManifestError('unsafe corrected profile path')
    for i, part in enumerate(parts):
        path = path / part
        _check(path, directory=i < len(parts)-1)
    try:
        return path.read_bytes()
    except OSError as exc:
        raise CorrectedManifestError('corrected profile read failed') from exc


def _check(path: Path, *, directory: bool) -> None:
    try:
        info = path.lstat()
    except OSError as exc:
        raise CorrectedManifestError('corrected profile component missing or unreadable') from exc
    if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
        raise CorrectedManifestError('corrected profile links/reparse points forbidden')
    if not (stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode)):
        raise CorrectedManifestError('corrected profile component type invalid')


def read_corrected_curation(path: Path) -> list[dict]:
    path = Path(path).absolute()
    if tuple(path.parts[-4:]) != ('gaza', 'editions', '2026-10-25', 'curation_manifest.json'):
        raise CorrectedManifestError('corrected profile owning path differs')
    root = path.parents[3]
    payloads = {}
    for relative, expected in PROFILE_HASHES.items():
        raw = _read_regular(root, relative)
        if hashlib.sha256(raw).hexdigest() != expected:
            raise CorrectedManifestError('corrected profile immutable content differs: '+relative)
        payloads[relative] = raw
    value = json.loads(payloads['gaza/editions/2026-10-25/curation_manifest.json'])
    # Exact bytes already validated; project only current reviewed fields.
    story = value[0]
    names = ('story_id', 'title', 'summary', 'source_record_ids', 'source_ids', 'source_urls', 'publisher_names')
    projected = {key: story[key] for key in names}
    projected['dedupe_classification'] = ASSESSMENT_STATUS
    projected['corrected_presentation_profile'] = CURATION_SCHEMA
    return [projected]


def read_memory_curation(path: Path, dispatch_slug: str) -> list[dict]:
    """Strict Gaza manifest interpretation; preserve legacy list semantics."""
    try:
        value = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError) as exc:
        raise CorrectedManifestError('curation manifest unreadable or malformed') from exc
    if _marked(value) or _has_correction(path):
        if dispatch_slug != 'gaza':
            raise CorrectedManifestError('corrected profile dispatch differs')
        return read_corrected_curation(path)
    if isinstance(value, list):
        if not all(isinstance(row, dict) for row in value):
            raise CorrectedManifestError('curation rows must be objects')
        return value
    if dispatch_slug != 'gaza' or not isinstance(value, dict) or value.get('schema_version') != CURATION_SCHEMA:
        raise CorrectedManifestError('unsupported curation envelope')
    return read_corrected_curation(path)


def reject_corrected_source_fallback(path: Path) -> None:
    """A corrected source envelope may never revive archived source prose."""
    try:
        value = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError) as exc:
        raise CorrectedManifestError('source manifest unreadable or malformed') from exc
    if hashlib.sha256(path.read_bytes()).hexdigest() == PROFILE_HASHES['gaza/editions/2026-10-25/sources_manifest.json']:
        raise CorrectedManifestError('Oct25 source fallback requires intact bound curation')
    if not isinstance(value, list) or _marked(value):
        raise CorrectedManifestError('source envelope requires intact bound corrected curation')


REPLICA_ROOTS = ('bluefern-dispatches-pages', 'output/site', 'output/dispatches')
ORIGINAL_BLOBS = {
 'index.html':'f2113893f3f887e67c845a1aa5544956522611ca',
 'edition_manifest.json':'68e7d520c0bdb1db35483fada28a2123c4bd0be9',
 'sources_manifest.json':'862200c99e429c9616468d78a13bdeb6138a8fe6',
 'curation_manifest.json':'d65254af847f962ef08235fd4be0dbbe573d2718',
 'source_quality_report.md':'7f804e6a11d12713c33f57fc4d21109d1a5892b8',
 'dedupe_report.json':'f68a33675d7003607f1724248494cc7238759997',
}


def _exact_directory(directory: Path, expected: set[str]) -> None:
    _check(directory,directory=True)
    seen=set()
    for path in directory.rglob('*'):
        relative=path.relative_to(directory).as_posix()
        if path.is_symlink() or relative not in expected:
            raise CorrectedManifestError('conflicting Oct25 replica inventory')
        _check(path,directory=False)
        seen.add(relative)
    if seen!=expected:raise CorrectedManifestError('conflicting partial Oct25 replica inventory')


def _replica_kind(base: Path) -> str:
    directory=base/'gaza/editions/2026-10-25'
    correction=base/Path(CORRECTION_PATH).parent
    present=directory.exists() or directory.is_symlink() or correction.exists() or correction.is_symlink()
    if not present:return 'absent'
    _exact_directory(directory,set(ORIGINAL_BLOBS))
    originals=True
    for name,oid in ORIGINAL_BLOBS.items():
        raw=_read_regular(base,'gaza/editions/2026-10-25/'+name)
        if hashlib.sha1(b'blob '+str(len(raw)).encode()+b'\0'+raw).hexdigest()!=oid:originals=False
    if originals and not correction.exists() and not correction.is_symlink():return 'original'
    try:
        _exact_directory(correction,{'index.html','correction.json'})
        read_corrected_curation(directory/'curation_manifest.json')
    except CorrectedManifestError as exc:
        raise CorrectedManifestError('conflicting immutable, unrecognized or partial Oct25 replica') from exc
    return 'corrected'


def _activation(root: Path):
    from .gaza_correction_activation import load_activation
    return load_activation(root)


def _pending_profile_present(base: Path) -> bool:
    """Pending bytes restrict use, never grant activation or publication."""
    if (base/CORRECTION_PATH).exists() or (base/CORRECTION_PATH).is_symlink():return True
    for relative,digest in PROFILE_HASHES.items():
        path=base/relative
        if not path.exists() and not path.is_symlink():continue
        raw=_read_regular(base,relative)
        if hashlib.sha256(raw).hexdigest()==digest:return True
        if path.suffix=='.json':
            try:value=json.loads(raw)
            except (ValueError,UnicodeError):continue
            if _marked(value):return True
    return False


def corrected_profile_for_outputs(root: Path, before_date: str) -> Path | None:
    """Only a committed approved activation changes normal history semantics.

    Pending correction bytes are refused, not interpreted as approval. Active
    replicas must be exact; stale original replicas never replace the correction.
    """
    if before_date <= '2026-10-25':return None
    if _activation(root) is None:
        if any(_pending_profile_present(root/r) for r in REPLICA_ROOTS):
            raise CorrectedManifestError('pending corrected profile lacks committed activation')
        return None
    corrected=[]
    for relative in REPLICA_ROOTS:
        base=root/relative
        if _replica_kind(base)=='corrected':
            corrected.append(base/'gaza/editions/2026-10-25/curation_manifest.json')
    if not corrected:
        raise CorrectedManifestError('required corrected Oct25 profile missing; stale fallback forbidden')
    return sorted(corrected,key=lambda path:str(path))[0]


def _marked(value):
    if isinstance(value, dict):
        return ('corrected_presentation_profile' in value or
                str(value.get('schema_version','')).startswith('gaza_corrected_'))
    return isinstance(value,list) and any(_marked(row) for row in value)


def _has_correction(path: Path) -> bool:
    if tuple(path.parts[-4:-1]) != ('gaza','editions','2026-10-25'):
        return False
    record = path.parents[3] / CORRECTION_PATH
    return record.exists() or record.is_symlink()


def prior_source_records(root: Path, manifests: list[tuple[str,Path]], before_date: str):
    chosen = corrected_profile_for_outputs(root, before_date)
    emitted=False
    for edition_date,path in manifests:
        if chosen is not None and edition_date == '2026-10-25':
            raw=_read_regular(path.parents[3], 'gaza/editions/2026-10-25/sources_manifest.json')
            expected=PROFILE_HASHES['gaza/editions/2026-10-25/sources_manifest.json']
            if hashlib.sha256(raw).hexdigest()!=expected and hashlib.sha1(b'blob '+str(len(raw)).encode()+b'\0'+raw).hexdigest()!=ORIGINAL_BLOBS['sources_manifest.json']:
                raise CorrectedManifestError('conflicting source replica')
            if emitted:continue
            emitted=True
            sourcepath=chosen.parent/'sources_manifest.json'
            rows=json.loads(_read_regular(sourcepath.parents[3],'gaza/editions/2026-10-25/sources_manifest.json'))
        else:
            rows=json.loads(path.read_text(encoding='utf-8'))
            if not isinstance(rows,list) or _marked(rows):
                raise CorrectedManifestError('unsupported or unbound source manifest')
        yield edition_date,rows


def assert_non_site_action_allowed(root: Path, edition_date: str, operation: str) -> None:
    """Deny audio/social before fallback, credentials, rendering or sending.

    These flags are restrictions, never an alternate approval mechanism. Any
    marker or correction record—including a damaged/unknown profile—is denied.
    """
    # The reviewed objective is site-only for this owning edition. Deleting
    # profile markers or restoring an old replica cannot create authority.
    if edition_date == '2026-10-25':
        raise CorrectedManifestError('corrected site-only profile forbids '+operation)
    for base in (root/'output/site',root/'bluefern-dispatches-pages',root/'output/dispatches'):
        directory=base/'gaza/editions'/edition_date
        if edition_date=='2026-10-25' and ((base/CORRECTION_PATH).exists() or (base/CORRECTION_PATH).is_symlink()):
            raise CorrectedManifestError('corrected site-only profile forbids '+operation)
        for name in ('curation_manifest.json','edition_manifest.json','sources_manifest.json'):
            path=directory/name
            if path.is_symlink():raise CorrectedManifestError('non-site input symlink forbidden')
            if not path.exists():continue
            try:value=json.loads(path.read_text(encoding='utf-8'))
            except (OSError,ValueError) as exc:raise CorrectedManifestError('non-site input unreadable') from exc
            altered_oct25 = False
            if edition_date=='2026-10-25' and name=='curation_manifest.json':
                raw=path.read_bytes()
                altered_oct25=hashlib.sha1(b'blob '+str(len(raw)).encode()+b'\0'+raw).hexdigest()!=ORIGINAL_BLOBS['curation_manifest.json']
            if altered_oct25 or _marked(value) or (isinstance(value,dict) and (value.get('audio_render_authorized') is False or value.get('social_publication_authorized') is False)):
                raise CorrectedManifestError('corrected site-only profile forbids '+operation)


def assert_generic_reconciliation_allowed(root: Path, site_root: Path, pages_root: Path | None) -> None:
    """Generic regeneration cannot restore stale bytes over this correction.

    Only a separately implemented immutable approval-bound release consumer may
    materialize this profile. This is a refusal, not publication authorization.
    """
    bases=[root/'output/dispatches',site_root,root/'bluefern-dispatches-pages']
    if pages_root is not None:bases.append(pages_root)
    if _activation(root) is not None or any(_pending_profile_present(base) for base in bases):
        raise CorrectedManifestError('corrected Oct25 profile requires approval-bound reconciliation')
