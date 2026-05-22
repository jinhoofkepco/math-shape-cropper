param(
    [Parameter(Mandatory = $true)]
    [string]$RepoUrl
)

$ErrorActionPreference = "Stop"

if (-not (Test-Path -LiteralPath ".git")) {
    throw "Run this script from the project root that contains the .git folder."
}

git branch -M main

$origin = git remote get-url origin 2>$null
if ($LASTEXITCODE -eq 0 -and $origin) {
    git remote set-url origin $RepoUrl
} else {
    git remote add origin $RepoUrl
}

git push -u origin main

