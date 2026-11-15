param(
    [Parameter(Mandatory = $true)][string]$Soc,
    [Parameter(Mandatory = $true)][string]$Bl,
    [Parameter(Mandatory = $true)][string]$Pit,
    [switch]$Probe
)
$ErrorActionPreference = 'Stop'
$env:PYTHONDONTWRITEBYTECODE = '1'

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$llvm = 'C:\Program Files\LLVM\bin'
$clang = Join-Path $llvm 'clang.exe'
$ldlld = Join-Path $llvm 'ld.lld.exe'
$objcopy = Join-Path $llvm 'llvm-objcopy.exe'

$name = if ($Probe) { 'loader_probe' } else { 'loader' }
$bl = (Resolve-Path $Bl).Path
$pit = (Resolve-Path $Pit).Path

Push-Location $root
try {
    python resolve.py --soc $Soc $bl
    if ($LASTEXITCODE -ne 0) { throw 'resolve failed' }
    python loader/bundle.py --soc $Soc $bl $pit loader/bundle.bin loader/bundle_expected.h
    if ($LASTEXITCODE -ne 0) { throw 'bundle failed' }

    $tmp = Join-Path $env:TEMP ("exynos-loader-" + [guid]::NewGuid().ToString('N'))
    New-Item -ItemType Directory -Path $tmp | Out-Null

    $flags = @(
        '--target=aarch64-none-elf', '-Oz', '-ffreestanding', '-nostdlib',
        '-fno-builtin', '-fno-stack-protector', '-fomit-frame-pointer',
        '-fno-unwind-tables', '-fno-asynchronous-unwind-tables',
        '-mgeneral-regs-only', '-fno-pic', '-fno-pie', '-fno-unroll-loops',
        '-ffunction-sections', '-fdata-sections', '-I.', '-Iloader'
    )
    if ($Probe) { $flags += '-DPROBE' }

    $objects = @(
        @('loader/start.S', 'start.o'),
        @('loader/payload.c', 'payload.o'),
        @('loader/lzma_dec.c', 'lzma.o')
    )
    foreach ($pair in $objects) {
        $out = Join-Path $tmp $pair[1]
        & $clang @flags -c $pair[0] -o $out
        if ($LASTEXITCODE -ne 0) { throw "compile failed: $($pair[0])" }
    }

    & $ldlld -T link.ld --build-id=none --gc-sections -o (Join-Path $tmp 'loader.elf') `
        (Join-Path $tmp 'start.o') (Join-Path $tmp 'payload.o') (Join-Path $tmp 'lzma.o')
    if ($LASTEXITCODE -ne 0) { throw 'link failed' }

    & $objcopy -O binary -j .code (Join-Path $tmp 'loader.elf') "$name.bin"
    if ($LASTEXITCODE -ne 0) { throw 'objcopy failed' }
    Copy-Item (Join-Path $tmp 'loader.elf') "$name.elf" -Force

    Get-Item "$name.bin", "$name.elf", loader/bundle.bin |
        Select-Object Name, Length
    Get-FileHash "$name.bin", loader/bundle.bin -Algorithm SHA256 |
        Select-Object Path, Hash
}
finally {
    Pop-Location
    if ($tmp -and (Test-Path $tmp)) { Remove-Item $tmp -Recurse -Force }
}
