<#
.SYNOPSIS
    Capture les réponses brutes de l'API locale d'un routeur Peplink.

.DESCRIPTION
    À exécuter depuis un PC situé sur le réseau du routeur. Ne nécessite RIEN d'installé
    (PowerShell natif Windows). Enregistre les réponses JRUTES dans un dossier, en occultant
    les jetons d'accès, pour permettre de caler précisément le mapping des champs.

    Deux modes d'authentification, selon ce qui est configuré sur le routeur :
      - Client API   : -ClientId            (admin web : "Grant API access to clients")
      - Compte admin : -Username            (session par cookie)

    Le secret du client, ou le mot de passe, est DEMANDÉ sans écho : passé en
    paramètre texte, il restait dans l'historique de la console.

.EXAMPLE
    .\capture_router_api.ps1 -RouterHost 192.168.50.1 -ClientId abc123

.EXAMPLE
    .\capture_router_api.ps1 -RouterHost 192.168.50.1 -Username admin

.NOTES
    Les fichiers produits peuvent contenir des identifiants matériels (IMEI, ICCID) et une
    position GPS. Relis-les avant de les transmettre.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$RouterHost,

    [string]$ClientId,
    [SecureString]$ClientSecret,
    [string]$Username,
    [SecureString]$Password,

    [string]$OutDir = "./capture-peplink"
)

$ErrorActionPreference = "Stop"

# --- Certificat auto-signé : les Peplink en présentent un par défaut ---
$isModernPS = $PSVersionTable.PSVersion.Major -ge 6
if (-not $isModernPS) {
    if (-not ("TrustAllCertsPolicy" -as [type])) {
        Add-Type @"
using System.Net;
using System.Security.Cryptography.X509Certificates;
public class TrustAllCertsPolicy : ICertificatePolicy {
    public bool CheckValidationResult(ServicePoint sp, X509Certificate cert, WebRequest req, int problem) { return true; }
}
"@
    }
    [System.Net.ServicePointManager]::CertificatePolicy = New-Object TrustAllCertsPolicy
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
}

function Invoke-Router {
    param(
        [string]$Url,
        [string]$Method = "GET",
        $Body,
        $Session
    )
    $params = @{
        Uri             = $Url
        Method          = $Method
        UseBasicParsing = $true
        TimeoutSec      = 20
    }
    if ($Body)    { $params.Body = $Body }
    if ($Session) { $params.WebSession = $Session }
    if ($isModernPS) { $params.SkipCertificateCheck = $true }
    return Invoke-WebRequest @params
}

New-Item -ItemType Directory -Force -Path $OutDir | Out-Null
$base = "https://$RouterHost"
$accessToken = $null
$session = $null

Write-Host "=== Routeur : $base" -ForegroundColor Cyan

function ConvertFrom-Secret([SecureString]$Secret) {
    [Runtime.InteropServices.Marshal]::PtrToStringAuto(
        [Runtime.InteropServices.Marshal]::SecureStringToBSTR($Secret))
}

# --- Authentification ---
if ($ClientId) {
    if (-not $ClientSecret) { $ClientSecret = Read-Host "Secret du client API" -AsSecureString }
    Write-Host "Authentification par client API..." -ForegroundColor Yellow
    try {
        # clientId / clientSecret, sans type d'octroi : les seuls noms que le
        # firmware accepte (mesuré sur MAX HD4 MBX 8.5.4 — les noms OAuth2
        # rendent 401 « Client not found »).
        $resp = Invoke-Router -Url "$base/api/auth.token.grant" -Method POST -Body @{
            clientId     = $ClientId
            clientSecret = (ConvertFrom-Secret $ClientSecret)
        }
        # Réponse d'auth conservée : sa forme exacte nous intéresse aussi.
        $redacted = $resp.Content -replace '("accessToken"\s*:\s*")[^"]*', '$1<OCCULTE>' `
                                  -replace '("access_token"\s*:\s*")[^"]*', '$1<OCCULTE>'
        $redacted | Out-File -FilePath (Join-Path $OutDir "auth.token.grant.json") -Encoding utf8
        $json = $resp.Content | ConvertFrom-Json
        if ($json.response.accessToken) { $accessToken = $json.response.accessToken }
        elseif ($json.access_token)     { $accessToken = $json.access_token }
        if ($accessToken) { Write-Host "  -> jeton obtenu" -ForegroundColor Green }
        else { Write-Warning "  -> aucun jeton trouvé : regarde auth.token.grant.json" }
    } catch {
        Write-Warning "Echec de l'authentification client : $($_.Exception.Message)"
    }
}
elseif ($Username) {
    if (-not $Password) { $Password = Read-Host "Mot de passe" -AsSecureString }
    Write-Host "Authentification par compte admin (cookie)..." -ForegroundColor Yellow
    try {
        $params = @{
            Uri             = "$base/api/login"
            Method          = "POST"
            Body            = (@{ username = $Username; password = (ConvertFrom-Secret $Password) } | ConvertTo-Json)
            ContentType     = "application/json"
            UseBasicParsing = $true
            TimeoutSec      = 20
            SessionVariable = "webSession"
        }
        if ($isModernPS) { $params.SkipCertificateCheck = $true }
        Invoke-WebRequest @params | Out-Null
        $session = $webSession
        Write-Host "  -> session ouverte" -ForegroundColor Green
    } catch {
        Write-Warning "Echec du login admin : $($_.Exception.Message)"
    }
}
else {
    Write-Warning "Aucun identifiant fourni : les appels echoueront probablement."
    Write-Host "Utilise -ClientId ou -Username : le secret sera demandé." -ForegroundColor Yellow
}

# --- Endpoints à capturer (status.wan.connection est LE plus important) ---
$endpoints = @(
    "status.wan.connection",
    "status.wan",
    "status.client",
    "info.location",
    "info.system",
    "status.system.info"
)

$ok = 0
foreach ($endpoint in $endpoints) {
    $url = "$base/api/$endpoint"
    # `accessToken` : `access_token` et l'en-tête Bearer sont refusés (mesuré).
    if ($accessToken) { $url = "$url`?accessToken=$accessToken" }
    try {
        $resp = Invoke-Router -Url $url -Session $session
        $content = $resp.Content
        if ($accessToken) { $content = $content.Replace($accessToken, "<OCCULTE>") }
        $content | Out-File -FilePath (Join-Path $OutDir "$endpoint.json") -Encoding utf8
        Write-Host ("  {0,-24} OK  ({1} octets)" -f $endpoint, $content.Length) -ForegroundColor Green
        $ok++
    } catch {
        Write-Host ("  {0,-24} ECHEC : {1}" -f $endpoint, $_.Exception.Message) -ForegroundColor DarkGray
    }
}

# --- Contexte utile pour caler le mapping ---
@"
Routeur    : $RouterHost
Date       : $(Get-Date -Format "yyyy-MM-dd HH:mm:ss")
Auth       : $(if ($accessToken) { "client API" } elseif ($session) { "compte admin" } else { "aucune" })
PowerShell : $($PSVersionTable.PSVersion)
Endpoints  : $ok / $($endpoints.Count) capturés

A COMPLETER A LA MAIN (important pour le mapping) :
  Modele du routeur  :
  Version firmware   :
"@ | Out-File -FilePath (Join-Path $OutDir "contexte.txt") -Encoding utf8

Write-Host ""
Write-Host "=== $ok endpoint(s) capture(s) dans : $(Resolve-Path $OutDir)" -ForegroundColor Cyan
Write-Host "Complete contexte.txt (modele + firmware), relis les fichiers, puis transmets le dossier." -ForegroundColor Yellow
