$ErrorActionPreference='Stop'
if (-not $env:CARGO_PROFILE_TEST_DEBUG) { $env:CARGO_PROFILE_TEST_DEBUG='0' }
$manifestPath = Join-Path $PSScriptRoot '..\src-tauri\Cargo.toml'
# Library test executables do not inherit Tauri's application manifest. The
# linked Windows GUI dependencies require Common Controls v6 even for unit tests.
$messages = & cargo test --manifest-path $manifestPath --locked --lib --no-run --message-format=json
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
$artifacts = $messages | ForEach-Object { try { $_ | ConvertFrom-Json } catch {} } |
    Where-Object { $_.reason -eq 'compiler-artifact' -and $_.profile.test -and $_.executable }
$mt = Get-Command mt.exe -ErrorAction SilentlyContinue
if (-not $mt) {
    $sdkBin = Join-Path ${env:ProgramFiles(x86)} 'Windows Kits\10\bin'
    $mt = Get-ChildItem -LiteralPath $sdkBin -Directory | Sort-Object Name -Descending |
        ForEach-Object { Join-Path $_.FullName 'x64\mt.exe' } | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
}
if (-not $mt) { throw 'Windows SDK mt.exe is required to prepare the test manifest.' }
foreach ($artifact in $artifacts) {
    $binary = $artifact.executable
    $manifest = Join-Path (Split-Path $binary) 'csswitch-test.manifest'
    $xml = '<?xml version="1.0" encoding="UTF-8"?><assembly xmlns="urn:schemas-microsoft-com:asm.v1" manifestVersion="1.0"><dependency><dependentAssembly><assemblyIdentity type="win32" name="Microsoft.Windows.Common-Controls" version="6.0.0.0" processorArchitecture="*" publicKeyToken="6595b64144ccf1df" language="*"/></dependentAssembly></dependency></assembly>'
    [IO.File]::WriteAllText($manifest, $xml)
    & $mt -nologo -manifest $manifest "-outputresource:$binary;#1"
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    & $binary
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}
if (-not $artifacts) { throw 'Cargo did not return a test executable.' }
