@echo off
setlocal enabledelayedexpansion
REM ============================================================================
REM  Capture des reponses brutes de l'API locale d'un routeur Peplink.
REM
REM  Fichier .cmd volontairement : les fichiers batch ne sont PAS soumis a la
REM  strategie d'execution PowerShell. Utilise curl.exe, natif sur Windows 10+.
REM  Rien a installer.
REM
REM  USAGE
REM    Compte admin  :  capture_router_api.cmd 192.168.50.1 admin
REM    Client API    :  capture_router_api.cmd 192.168.50.1 -client ID
REM
REM  Le mot de passe (ou le secret du client) est DEMANDE, sans echo : sur la
REM  ligne de commande il restait dans l'historique et la liste des processus.
REM  Il passe a curl par un fichier temporaire, efface aussitot.
REM
REM  Les fichiers produits peuvent contenir IMEI / ICCID / position GPS.
REM  Relis-les avant de les transmettre.
REM ============================================================================

if "%~1"=="" goto :usage

set "RHOST=%~1"
set "OUTDIR=%~dp0capture-peplink"
set "BASE=https://%RHOST%"
set "CURL=%SystemRoot%\System32\curl.exe"
set "TOKEN="
set "AUTHMODE=aucune"

if not exist "%CURL%" (
  echo [ERREUR] curl.exe introuvable dans %SystemRoot%\System32\
  echo          Windows 10 version 1803 ou superieure est requis.
  exit /b 1
)

if not exist "%OUTDIR%" mkdir "%OUTDIR%"

echo.
echo === Routeur : %BASE%
echo === Sortie  : %OUTDIR%
echo.

REM --- Authentification -------------------------------------------------------
if /i "%~2"=="-client" goto :auth_client
if not "%~2"=="" goto :auth_admin
echo [!] Aucun identifiant fourni : les appels vont probablement echouer.
goto :fetch

:auth_admin
set "AUTHMODE=compte admin"
echo Authentification par compte admin (cookie)...
set "PUSER=%~2"
set "CORPS=%OUTDIR%\login.body"
powershell -NoProfile -Command "$s = Read-Host 'Mot de passe' -AsSecureString; $p = [Runtime.InteropServices.Marshal]::PtrToStringAuto([Runtime.InteropServices.Marshal]::SecureStringToBSTR($s)); [IO.File]::WriteAllText($env:CORPS, (@{ username = $env:PUSER; password = $p } | ConvertTo-Json -Compress))"
"%CURL%" -k -s -c "%OUTDIR%\cookies.txt" -X POST "%BASE%/api/login" ^
  -H "Content-Type: application/json" ^
  --data "@%CORPS%" ^
  -o "%OUTDIR%\login.json"
if exist "%CORPS%" del "%CORPS%"
if errorlevel 1 (
  echo   -^> ECHEC de la connexion au routeur.
) else (
  echo   -^> session ouverte ^(voir login.json^)
)
goto :fetch

:auth_client
set "AUTHMODE=client API"
echo Authentification par client API...
REM clientId / clientSecret, sans type d'octroi : les seuls noms que le firmware
REM accepte (mesure sur MAX HD4 MBX 8.5.4 -- les noms OAuth2 rendent 401 "Client not found").
set "PCLIENT=%~3"
set "CORPS=%OUTDIR%\auth.body"
powershell -NoProfile -Command "$s = Read-Host 'Secret du client API' -AsSecureString; $p = [Runtime.InteropServices.Marshal]::PtrToStringAuto([Runtime.InteropServices.Marshal]::SecureStringToBSTR($s)); [IO.File]::WriteAllText($env:CORPS, 'clientId=' + [uri]::EscapeDataString($env:PCLIENT) + '&clientSecret=' + [uri]::EscapeDataString($p))"
"%CURL%" -k -s -X POST "%BASE%/api/auth.token.grant" ^
  --data "@%CORPS%" ^
  -o "%OUTDIR%\auth.token.grant.json"
if exist "%CORPS%" del "%CORPS%"
if not exist "%OUTDIR%\auth.token.grant.json" (
  echo   -^> ECHEC : aucune reponse.
  goto :fetch
)
REM Extraction du jeton depuis le JSON (une seule ligne attendue).
for /f "tokens=2 delims=:," %%A in ('findstr /i "accessToken" "%OUTDIR%\auth.token.grant.json"') do (
  if not defined TOKEN (
    set "TOKEN=%%~A"
    set "TOKEN=!TOKEN:"=!"
    set "TOKEN=!TOKEN: =!"
  )
)
if defined TOKEN (
  echo   -^> jeton obtenu
) else (
  echo   -^> aucun jeton trouve : regarde auth.token.grant.json
)
goto :fetch

REM --- Recuperation des endpoints --------------------------------------------
:fetch
echo.
echo Recuperation des endpoints...
set /a OK=0
set /a TOTAL=0

for %%E in (status.wan.connection status.wan status.client info.location info.system status.system.info) do (
  set /a TOTAL+=1
  set "URL=%BASE%/api/%%E"
  if defined TOKEN set "URL=!URL!?accessToken=!TOKEN!"
  if exist "%OUTDIR%\cookies.txt" (
    "%CURL%" -k -s -f -b "%OUTDIR%\cookies.txt" "!URL!" -o "%OUTDIR%\%%E.json"
  ) else (
    "%CURL%" -k -s -f "!URL!" -o "%OUTDIR%\%%E.json"
  )
  if errorlevel 1 (
    echo   %%E : ECHEC
    if exist "%OUTDIR%\%%E.json" del "%OUTDIR%\%%E.json"
  ) else (
    echo   %%E : OK
    set /a OK+=1
  )
)

REM --- Contexte ---------------------------------------------------------------
> "%OUTDIR%\contexte.txt" (
  echo Routeur   : %RHOST%
  echo Date      : %DATE% %TIME%
  echo Auth      : %AUTHMODE%
  echo Endpoints : !OK! / !TOTAL! captures
  echo.
  echo A COMPLETER A LA MAIN ^(important pour le mapping^) :
  echo   Modele du routeur  :
  echo   Version firmware   :
)

if exist "%OUTDIR%\cookies.txt" del "%OUTDIR%\cookies.txt"

echo.
echo === !OK! endpoint(s) capture(s) dans : %OUTDIR%
echo.
echo A FAIRE AVANT DE TRANSMETTRE :
echo   1. Completer contexte.txt ^(modele + firmware^)
echo   2. Relire les fichiers ^(IMEI / ICCID / GPS^)
if defined TOKEN echo   3. auth.token.grant.json CONTIENT UN JETON : supprime-le ou occulte-le
echo.
goto :eof

:usage
echo.
echo Capture des reponses de l'API locale d'un routeur Peplink.
echo.
echo   Compte admin :  capture_router_api.cmd ADRESSE_IP UTILISATEUR
echo   Client API   :  capture_router_api.cmd ADRESSE_IP -client CLIENT_ID
echo   Le mot de passe, ou le secret du client, est demande sans echo.
echo.
echo Exemple :
echo   capture_router_api.cmd 192.168.50.1 admin monmotdepasse
echo.
exit /b 1
