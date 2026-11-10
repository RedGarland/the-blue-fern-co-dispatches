"""LOCAL PROPOSAL ONLY: exact seven-path Nov10 visual QA, never publication."""
from __future__ import annotations
import argparse
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import subprocess
import sys

PAGES_SHA = '1c050908845bf1f1657891015fa5b52a7e1c7ddb'
SOURCE_SHA = '3b55ce385a7c6f7f879986a753ec7808956fa4ef'
PLAYWRIGHT_VERSION = '1.64.1'
RESERVE = 2 * 1024**3
EXPECTED = {'gaza/index.html': ('170a0cb139c5d1d5557b1db63acc3f70a88e1e1202e3aee9c57d1d2e79a463d0', 'abff83ae487f5819d8ec5338e5d6e879b6adc93ae21a65a57f1a0b967ddaa3fa'), 'gaza/archive.html': ('abe3dc4a8fd1896fcadc1b190ba79ff7b4e6af26f3ac6f8e1824003b09ac23ba', '9ec7b4209a20f28e4702dacc8df0da7feac1e85d169df28b29cfa8f31b6cbde2'), 'gaza/rss.xml': ('f5d3660ed794654780c32f7b172168879d3a36f6dc91d0d9e769df2dcef3d0f6', '5529078d934c6d1b26a4c45b06132c3ec23b98f945f0d6b54342ac649180abfb'), 'gaza/editions/2026-11-10/index.html': (None, '41807d3138e86042ffa11f0474aa4acfd378a1867fa5539d62693d6d3d333c74'), 'gaza/editions/2026-11-10/edition_manifest.json': (None, '2af0d0787a980cf7201e351326a1d89225f5ee8364a011491e31e09ff88a8bb8'), 'gaza/editions/2026-11-10/curation_manifest.json': (None, '46e60c457e4638aeed2aad666034cd027f2cb92b988dbfb155f7b134171b1ecb'), 'gaza/editions/2026-11-10/sources_manifest.json': (None, '5f2c3dbb33679c13ece32d160b4e2d94fa6b71280fc157ca506d005b4a84806a')}
MANIFEST_SHA256 = 'abdfbb7a7f89dd1b296a6bb21409a2dcc1dd03d4224d830913a48bf88e37add8'


def digest(path):
    h = hashlib.sha256()
    with path.open('rb') as f:
        for block in iter(lambda: f.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def git(root, *args):
    return subprocess.check_output(['git', '-C', str(root), *args])


def no_symlinks(path):
    path = Path(os.path.abspath(path))
    for current in [*reversed(path.parents), path]:
        if current.is_symlink():
            raise ValueError(f'symlink forbidden: {current}')
    return path


def safe_name(name):
    p = PurePosixPath(name)
    if (not name or p.is_absolute() or '..' in p.parts or '.git' in p.parts
            or '\\' in name or str(p) != name):
        raise ValueError('unsafe payload/tracked path')
    return name


def require_checkout(root, sha):
    no_symlinks(root)
    if git(root, 'rev-parse', 'HEAD').decode().strip() != sha:
        raise ValueError('checkout HEAD differs from exact pinned commit')
    if git(root, 'status', '--porcelain', '--untracked-files=all', '--ignored=matching').strip():
        raise ValueError('checkout contains tracked, untracked or ignored changes')


def tracked_inventory(root):
    result = {}
    for entry in git(root, 'ls-tree', '-rz', '--full-tree', 'HEAD').split(b'\0'):
        if not entry:
            continue
        metadata, rawname = entry.split(b'\t', 1)
        mode, kind, blob = metadata.split()
        if kind != b'blob' or mode not in (b'100644', b'100755'):
            raise ValueError('baseline contains symlink, submodule or unsupported entry')
        name = safe_name(rawname.decode('utf-8'))
        path = no_symlinks(root / name)
        if not path.is_file():
            raise ValueError('baseline tracked leaf is not regular file')
        if git(root, 'hash-object', '--no-filters', str(path)).strip() != blob:
            raise ValueError('baseline worktree bytes differ from pinned Git blob')
        result[name] = digest(path)
    return result


def file_inventory(root):
    result = {}
    for directory, dirs, files in os.walk(root, followlinks=False):
        for name in dirs + files:
            no_symlinks(Path(directory) / name)
        for name in files:
            path = Path(directory) / name
            if not path.is_file():
                raise ValueError('nonregular staged leaf')
            result[path.relative_to(root).as_posix()] = digest(path)
    return result


def verify_routes(result, expected, screenshots):
    if not result.get('ok') or set(result.get('paths_checked', [])) != set(expected):
        raise ValueError('unchanged visual gates or exact route coverage failed')
    pages = result.get('pages', [])
    if len(pages) != len(expected) or {p.get('path') for p in pages} != set(expected):
        raise ValueError('missing per-route evidence')
    hashes = result.get('screenshot_hashes', {})
    if len(hashes) != len(expected):
        raise ValueError('missing screenshot hashes')
    for page in pages:
        if page.get('status') != 200 or not page.get('screenshot'):
            raise ValueError('route lacks HTTP200/screenshot')
        path = no_symlinks(Path(page['screenshot']))
        if not path.is_relative_to(screenshots.resolve()) or not path.is_file():
            raise ValueError('screenshot missing or escaped evidence directory')
        if hashes.get(str(path)) != digest(path):
            raise ValueError('screenshot hash mismatch')


def stage(baseline, payload, manifest, candidate):
    for path in (baseline, payload, manifest, candidate.parent):
        no_symlinks(path)
    if candidate.exists() or candidate.is_symlink():
        raise ValueError('candidate destination exists; no reuse/deletion')
    if any(candidate.resolve().is_relative_to(p.resolve()) for p in (baseline, payload)):
        raise ValueError('candidate must be outside immutable inputs')
    if digest(manifest) != MANIFEST_SHA256:
        raise ValueError('exact approved manifest bytes changed')
    require_checkout(baseline, PAGES_SHA)
    before = tracked_inventory(baseline)
    if file_inventory(payload) != {name: hashes[1] for name, hashes in EXPECTED.items()}:
        raise ValueError('payload must contain exactly seven expected regular files/hashes')
    for name, (old, new) in EXPECTED.items():
        safe_name(name)
        if before.get(name) != old:
            raise ValueError('before hash or absence differs from pinned manifest')
    projected = sum((baseline / name).stat().st_size for name in before)
    projected += sum((payload / name).stat().st_size for name in EXPECTED)
    if shutil.disk_usage(candidate.parent).free < projected + RESERVE:
        raise ValueError('projected stage plus unchanged2GiB reserve unavailable')
    candidate.mkdir()
    expected = dict(before)
    expected.update({name: hashes[1] for name, hashes in EXPECTED.items()})
    for name in expected:
        source = payload / name if name in EXPECTED else baseline / name
        dest = candidate / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        with source.open('rb') as src, dest.open('xb') as dst:
            shutil.copyfileobj(src, dst)
    if file_inventory(candidate) != expected:
        raise ValueError('stage differs from exact baseline-plus-seven expected inventory')
    if tracked_inventory(baseline) != before:
        raise ValueError('baseline mutated during stage')
    require_checkout(baseline, PAGES_SHA)
    return {'baseline_sha': PAGES_SHA, 'source_sha': SOURCE_SHA,
            'changed_paths': sorted(EXPECTED), 'unchanged_paths_verified': len(before) - 3,
            'all_stage_sha256': expected, 'source_css_substituted': False}


def main():
    parser = argparse.ArgumentParser()
    for name in ('source', 'baseline', 'payload', 'manifest', 'candidate', 'evidence'):
        parser.add_argument('--' + name, type=Path, required=True)
    args = parser.parse_args()
    for name, value in vars(args).items():
        setattr(args, name, no_symlinks(value))
    if args.evidence.exists():
        raise ValueError('evidence destination already exists')
    args.evidence.mkdir(parents=True)
    report = {'ok': False, 'publication_performed': False, 'deployment_performed': False}
    try:
        require_checkout(args.source, SOURCE_SHA)
        report['stage'] = stage(args.baseline, args.payload, args.manifest, args.candidate)
        version = importlib.metadata.version('playwright')
        if version != PLAYWRIGHT_VERSION:
            raise ValueError('Playwright version differs from pinned browser package')
        report['playwright_version'] = version
        sys.path.insert(0, str(args.source / 'src'))
        from bluefern_dispatches.public_site_visuals import validate_public_site_visuals, _required_paths
        # Keep unchanged validator defaults. No monkeypatches, CSS overrides,
        # screenshot-only stand-ins, system browser fallback, or gate suppression.
        report['default_routes'] = validate_public_site_visuals(
            args.candidate, screenshot_dir=args.evidence / 'default-screenshots')
        report['nov10'] = validate_public_site_visuals(
            args.candidate, screenshot_dir=args.evidence / 'nov10-screenshot',
            paths=['/gaza/editions/2026-11-10/'])
        defaults = _required_paths(args.candidate)
        if len(defaults) != 7 or len(set(defaults)) != 7:
            raise ValueError('pinned baseline must retain all seven default routes')
        verify_routes(report['default_routes'], defaults, args.evidence / 'default-screenshots')
        verify_routes(report['nov10'], ['/gaza/editions/2026-11-10/'], args.evidence / 'nov10-screenshot')
        if file_inventory(args.candidate) != report['stage']['all_stage_sha256']:
            raise ValueError('stage mutated during browser validation')
        require_checkout(args.source, SOURCE_SHA)
        require_checkout(args.baseline, PAGES_SHA)
        report['ok'] = True
    except Exception as exc:
        report['error'] = f'{type(exc).__name__}: {exc}'
    finally:
        (args.evidence / 'exact-nov10-visual-report.json').write_text(json.dumps(report, indent=2) + '\n')
    return 0 if report['ok'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
