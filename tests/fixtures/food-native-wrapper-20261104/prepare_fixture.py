"""Prepare only. This script never starts PowerShell or any child process."""
from pathlib import Path
import hashlib, json
ROOT = Path(__file__).resolve().parent
EXPECTED = '59422d8995cd68b33b41d7c51f6541b01c85f67ebc2f603b379c8513d55dc057'
def prepare():
    original = (ROOT/'candidate.ps1').read_bytes()
    assert len(original) == 22152 and hashlib.sha256(original).hexdigest() == EXPECTED
    source = original.decode('utf-8-sig')
    newline = '\r\n' if '\r\n' in source else '\n'
    changes = [
        ('$process = Start-Process -FilePath', '$process = Invoke-FixtureChildProcess -FilePath'),
        ('$remote = & git ls-remote origin', '$remote = Invoke-FixtureGit ls-remote origin'),
        ('exit $ExitCode', '$FixtureState.ExitCode = [int]$ExitCode; return'),
    ]
    edits = []
    for before, after in changes:
        assert source.count(before) == 1, (before, source.count(before))
        source = source.replace(before, after)
        edits.append({'before': before, 'after': after})
    gate = (ROOT/'fixture-gate.ps1').read_text().replace('\n',newline)
    anchor = 'Set-StrictMode -Version 3.0'
    assert source.count(anchor)==1
    source = source.replace(anchor,gate+newline+anchor)
    output=source.encode('utf-8')
    (ROOT/'instrumented-candidate.ps1').write_bytes(output)
    # Reverse instrumentation to establish no omitted/reimplemented control flow.
    reverse=source.replace(gate+newline+anchor,anchor,1)
    for before,after in reversed(changes): reverse=reverse.replace(after,before,1)
    assert reverse == original.decode('utf-8-sig')
    result={'candidate_sha256':EXPECTED,'candidate_bytes':len(original),
            'instrumented_sha256':hashlib.sha256(output).hexdigest(),
            'edits':edits,'inserted_gate_sha256':hashlib.sha256(gate.encode()).hexdigest(),
            'reverse_transformation_matches_exact_source_text':True,
            'native_execution':'not_run','production_changes':False}
    (ROOT/'preparation-manifest.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result,indent=2))
if __name__=='__main__': prepare()
