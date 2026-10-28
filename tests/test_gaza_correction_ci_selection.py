"""Ensure the explicit CI selection actually executes all new regression suites."""
from pathlib import Path
import shlex
import yaml

def test_all_correction_regressions_are_in_explicit_ci_selection():
    root=Path(__file__).resolve().parents[1]
    workflow=yaml.safe_load((root/'.github/workflows/dispatch-validation.yml').read_text())
    step=next(s for s in workflow['jobs']['validate']['steps'] if s.get('name')=='Run focused validation tests')
    selected=set(shlex.split(step['run']))
    required={'tests/test_gaza_correction_runtime_proofs.py', 'tests/test_gaza_correction_replica_matrix.py', 'tests/test_gaza_correction_activation.py', 'tests/test_gaza_oct25_release_package.py', 'tests/test_gaza_conditional_consent.py', 'tests/test_gaza_corrected_reader_contract.py', 'tests/test_gaza_oct25_remote_plan.py', 'tests/test_gaza_correction_ci_selection.py', 'tests/test_gaza_public_history_reconciliation.py'}
    assert required<=selected,sorted(required-selected)
    assert all((root/path).is_file() for path in required)
