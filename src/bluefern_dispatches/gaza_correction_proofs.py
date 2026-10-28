"""Read-only correction proof helpers; no preparation, approval or release API."""
from __future__ import annotations
import html,json,os,subprocess
from pathlib import Path
from typing import Any
from urllib.parse import unquote
from xml.etree import ElementTree
from . import gaza_historical_correction as legacy
Error=legacy.CorrectionValidationError
NO_AUDIO_AGGREGATE_PATHS=('gaza/audio/index.html','gaza/podcast.xml','gaza/audio/podcast.xml','gaza/flash-briefing.json')


def _is_reparse_or_symlink(path: Path) -> bool:
    """Treat link/reparse entries as unknown, never as proof of absence."""
    try:
        stat = path.lstat()
    except FileNotFoundError:
        return False
    except OSError as exc:
        raise Error(f"cannot inspect audio path safely: {path}: {exc}") from exc
    # st_file_attributes is available on Windows and carries the reparse bit;
    # is_symlink covers normal links on every supported platform.
    return path.is_symlink() or bool(getattr(stat, "st_file_attributes", 0) & 0x400)

def _has_reparse_ancestor(root: Path, path: Path) -> bool:
    root = Path(os.path.abspath(root))
    path = Path(os.path.abspath(path))
    try:
        relative = path.relative_to(root)
    except ValueError as exc:
        raise Error("audio validation path escapes Pages") from exc
    current = root
    ancestors = []
    while True:
        ancestors.append(current)
        if current.parent == current:
            break
        current = current.parent
    for ancestor in reversed(ancestors):
        if _is_reparse_or_symlink(ancestor):
            return True
    current = root
    for part in relative.parts:
        current = current / part
        if _is_reparse_or_symlink(current):
            return True
    return False

def _lexical_repo_path(root: Path, relative: str) -> Path:
    """Return a non-resolved path after checking every lexical component."""
    root = Path(os.path.abspath(root))
    rel = Path(relative.replace("/", os.sep))
    if rel.is_absolute() or ".." in rel.parts:
        raise Error("audio validation path escapes Pages")
    candidate = Path(os.path.abspath(root / rel))
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise Error("audio validation path escapes Pages") from exc
    if _has_reparse_ancestor(root, candidate):
        raise Error(f"none_present audio path contains a reparse or symlink component: {relative}")
    return candidate

def _pinned_tree_entry(pages: Path, revision: str, relative: str) -> tuple[str, str, str] | None:
    """Read one exact pinned-tree entry; non-missing Git failures are fatal."""
    try:
        result = subprocess.run(
            ["git", "-C", str(pages), "ls-tree", "-z", "--full-tree", revision, "--", relative],
            check=False,
            capture_output=True,
        )
    except OSError as exc:
        raise Error(f"none_present pinned tree inspection failed: {relative}") from exc
    if result.returncode:
        detail = result.stderr.decode("utf-8", errors="replace").strip()
        raise Error(f"none_present pinned tree inspection failed: {relative}: {detail}")
    for row in result.stdout.split(b"\x00"):
        if not row:
            continue
        try:
            metadata, path = row.split(b"\t", 1)
            mode, object_id, kind = metadata.decode("ascii").split(" ", 2)
            path_text = path.decode("utf-8")
        except (UnicodeDecodeError, ValueError) as exc:
            raise Error(f"none_present pinned tree entry is malformed: {relative}") from exc
        if path_text == relative:
            return mode, object_id, kind
    return None

def _pinned_cat_file_presence(pages: Path, revision: str, relative: str) -> bool:
    """Use cat-file only with an explicit missing-path distinction."""
    try:
        legacy._git(pages, "cat-file", "-e", f"{revision}:{relative}")
    except Error as exc:
        message = str(exc)
        if (f"path '{relative}' does not exist in '" in message
                or f"path '{relative}' exists on disk, but not in '" in message):
            return False
        raise Error(f"none_present pinned tree validation failed: {relative}: {message}") from exc
    return True

def _decoded_aggregate_variants(text: str, path: Path) -> set[str]:
    """Return decoded spellings used for fail-closed dated-reference checks."""
    def expand(values: set[str]) -> set[str]:
        variants = set(values)
        # Repeat the cheap decoders so combinations such as JSON-escaped,
        # slash-escaped and percent-encoded URLs are all inspected.
        for _ in range(3):
            additions = set()
            for value in variants:
                additions.update({html.unescape(value), value.replace("\\/", "/"), unquote(value)})
            variants.update(additions)
        return variants

    variants = expand({text})

    def json_strings(value: Any) -> set[str]:
        if isinstance(value, str):
            return {value}
        if isinstance(value, dict):
            values = set()
            for key, item in value.items():
                values.update(json_strings(key))
                values.update(json_strings(item))
            return values
        if isinstance(value, list):
            values = set()
            for item in value:
                values.update(json_strings(item))
            return values
        return set()

    def xml_strings(root: ElementTree.Element) -> set[str]:
        values = set()
        for element in root.iter():
            if element.text:
                values.add(element.text)
            if element.tail:
                values.add(element.tail)
            values.update(element.attrib.values())
        return values

    # Repeat the cheap decoders so combinations such as JSON-escaped,
    # slash-escaped and percent-encoded URLs are all inspected.
    if path.suffix.lower() == ".json":
        try:
            decoded = json.loads(text)
        except (TypeError, ValueError) as exc:
            raise Error(f"none_present aggregate is malformed JSON: {path.as_posix()}") from exc
        variants.update(expand(json_strings(decoded)))
    elif path.suffix.lower() == ".xml":
        try:
            decoded = ElementTree.fromstring(text.encode("utf-8"))
        except ElementTree.ParseError as exc:
            raise Error(f"none_present aggregate is malformed XML: {path.as_posix()}") from exc
        variants.update(expand(xml_strings(decoded)))
    elif path.suffix.lower() == ".html":
        try:
            legacy._StrictHtmlTreeParser(text).finish()
        except Exception as exc:
            raise Error(f"none_present aggregate is malformed HTML: {path.as_posix()}") from exc
    return variants

def _validate_absent_audio_profile(pages: Path, date: str, pinned_head: str | None = None) -> None:
    pinned_head = pinned_head or legacy._git(pages, "rev-parse", "HEAD")
    dated = (
        f"gaza/audio/{date}.json",
        f"gaza/audio/{date}-transcript.html",
        f"gaza/audio/{date}.mp3",
    )
    present = []
    for rel in dated:
        lexical = _lexical_repo_path(pages, rel)
        path = legacy._repo_path(pages, rel, "edition correction artifact")
        tree_entry = _pinned_tree_entry(pages, pinned_head, rel)
        cat_present = _pinned_cat_file_presence(pages, pinned_head, rel)
        if (tree_entry is None) != (not cat_present):
            raise Error(f"none_present pinned tree inventory is contradictory: {rel}")
        if tree_entry is not None:
            present.append(rel)
        try:
            exists = lexical.exists()
        except OSError as exc:
            raise Error(f"none_present audio profile cannot prove absence: {rel}") from exc
        if exists:
            # A directory, reparse point, symlink, or regular file is all
            # non-absence.  Do not follow or classify any of them as absent.
            if rel not in present:
                present.append(rel)
    if present:
        raise Error("none_present audio profile conflicts with dated audio: " + ", ".join(present))
    references = []
    for rel in NO_AUDIO_AGGREGATE_PATHS:
        lexical = _lexical_repo_path(pages, rel)
        path = legacy._repo_path(pages, rel, "edition correction artifact")
        tree_entry = _pinned_tree_entry(pages, pinned_head, rel)
        cat_present = _pinned_cat_file_presence(pages, pinned_head, rel)
        if tree_entry is None or not cat_present:
            raise Error(f"none_present audio aggregate is absent from pinned tree: {rel}")
        if not lexical.is_file():
            raise Error(f"none_present audio profile cannot validate missing aggregate: {rel}")
        text = path.read_text(encoding="utf-8-sig", errors="strict")
        variants = _decoded_aggregate_variants(text, path)
        if any(date in variant or f"/gaza/audio/{date}" in variant for variant in variants):
            references.append(rel)
    if references:
        raise Error("none_present audio profile has dated aggregate references: " + ", ".join(references))

def _one(nodes: list[Any], label: str) -> Any:
    if len(nodes) != 1:
        raise Error(f"{label} is missing or ambiguous")
    return nodes[0]

def _text(node: Any) -> str:
    return legacy._normalized_html_text(node)

def _within(node: Any, owner: Any) -> bool:
    return legacy._is_descendant(node, owner)

def _regular(root: Path, relative: str) -> Path:
    path = _lexical_repo_path(root, relative)
    if not path.is_file():
        raise Error('snapshot input is not a regular file: ' + relative)
    return path

def _inventory(root:Path, files):
    expected=set(files);actual=set();dirs={p.as_posix() for name in expected for p in Path(name).parents if p!=Path('.')}
    for path in root.rglob('*'):
        rel=path.relative_to(root).as_posix()
        if _is_reparse_or_symlink(path):raise Error('editorial packet contains a link/reparse entry')
        if path.is_dir():
            if rel not in dirs:raise Error('editorial packet contains unexpected directory')
        elif path.is_file():actual.add(rel)
        else:raise Error('editorial packet contains unsupported filesystem entry')
    if actual!=expected:raise Error('editorial packet exact file inventory differs')
    for relative,raw in files.items():
        if _regular(root,relative).read_bytes()!=raw:raise Error('editorial packet differs from exact deterministic replay: '+relative)
