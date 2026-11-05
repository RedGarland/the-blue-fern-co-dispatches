# Fixture instrumentation guard, before any original wrapper runtime statement.
# Boundary commands are renamed, so absent test doubles fail instead of falling
# through to the real Start-Process or git executable.
if ($null -eq (Get-Variable FixtureState -ErrorAction SilentlyContinue)) { throw 'Fixture harness state required' }
if ($FixtureState.Token -cne 'food-wrapper-control-flow-fixture-v1') { throw 'Invalid fixture state' }
foreach ($rootName in @('OperatorRoot','FoodRoot','CareRoot','StatusCheckout')) {
    $provided = [IO.Path]::GetFullPath([string](Get-Variable -Name $rootName -ValueOnly))
    if ($provided -cne $FixtureState.Roots[$rootName]) { throw "Non-fixture root refused: $rootName" }
}
if ($RunId -cne $FixtureState.RunId -or $EditionDate -cne $FixtureState.EditionDate) { throw 'Unexpected fixture run binding' }
foreach ($boundary in @('Invoke-FixtureChildProcess','Invoke-FixtureGit')) {
    if ((Get-Command $boundary -ErrorAction Stop).CommandType -ne 'Function') { throw 'Fixture boundary is not a function' }
}
