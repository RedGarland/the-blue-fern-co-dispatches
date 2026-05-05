import shutil
import uuid
import json
from pathlib import Path

repo = Path(__file__).resolve().parents[1]
test_root = repo / 'output' / 'debug-run' / uuid.uuid4().hex
work = test_root / 'repo'
shutil.copytree(repo / 'assets', work / 'assets')
backup_root = test_root / 'dispatches-bluefern-backups'

from bluefern_dispatches.generator import build_site

res = build_site(work, dry_run=False, backup_root=backup_root)
print('RESULT:', json.dumps(res, default=str))

print('\nOUTPUT SITE TREE:')
site_root = work / 'output' / 'site'
if site_root.exists():
    for p in sorted(site_root.rglob('*')):
        print(p.relative_to(work))
else:
    print('(no site output)')

print('\nBACKUP TREE:')
if backup_root.exists():
    for p in sorted(backup_root.rglob('*')):
        print(p.relative_to(backup_root))
else:
    print('(no backup dir)')
