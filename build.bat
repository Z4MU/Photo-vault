@echo off
REM ─────────────────────────────────────────────────────────────────────────
REM PhotoVault - build.bat
REM ─────────────────────────────────────────────────────────────────────────

title PhotoVault - Build

echo.
echo  ====================================================
echo   PhotoVault - Generando ejecutable v1.0
echo  ====================================================
echo.

REM ── Buscar Python en ubicaciones comunes ─────────────────────────────────
set PYTHON_EXE=

REM 1. Python en PATH estándar
python --version >nul 2>&1
if not errorlevel 1 (
    set PYTHON_EXE=python
    goto :found_python
)

REM 2. py launcher (instalador oficial de python.org)
py --version >nul 2>&1
if not errorlevel 1 (
    set PYTHON_EXE=py
    goto :found_python
)

REM 3. Ruta específica de tu instalación (detectada del error)
if exist "%LOCALAPPDATA%\Python\pythoncore-3.14-64\python.exe" (
    set PYTHON_EXE=%LOCALAPPDATA%\Python\pythoncore-3.14-64\python.exe
    goto :found_python
)

REM 4. Buscar cualquier pythoncore en LOCALAPPDATA\Python\
for /d %%D in ("%LOCALAPPDATA%\Python\pythoncore-*") do (
    if exist "%%D\python.exe" (
        set PYTHON_EXE=%%D\python.exe
        goto :found_python
    )
)

REM 5. Rutas típicas del instalador oficial
for %%V in (3.14 3.13 3.12 3.11 3.10) do (
    if exist "%LOCALAPPDATA%\Programs\Python\Python%%V\python.exe" (
        set PYTHON_EXE=%LOCALAPPDATA%\Programs\Python\Python%%V\python.exe
        goto :found_python
    )
    if exist "C:\Python%%V\python.exe" (
        set PYTHON_EXE=C:\Python%%V\python.exe
        goto :found_python
    )
)

REM 6. No encontrado
echo [ERROR] No se encontro Python en ninguna ubicacion conocida.
echo.
echo  Opciones para resolverlo:
echo  A) Abre este archivo con Bloc de notas, busca la linea que dice
echo     SET PYTHON_EXE= y agrega la ruta completa de tu python.exe
echo     Ejemplo:  set PYTHON_EXE=C:\MiPython\python.exe
echo.
echo  B) Reinstala Python desde https://python.org marcando
echo     "Add Python to PATH"
echo.
pause
exit /b 1

:found_python
echo [OK] Python encontrado: %PYTHON_EXE%
"%PYTHON_EXE%" --version
echo.

REM ── Buscar pip ────────────────────────────────────────────────────────────
set PIP_CMD="%PYTHON_EXE%" -m pip

REM ── Verificar / instalar PyInstaller ─────────────────────────────────────
echo [1/4] Verificando PyInstaller...
"%PYTHON_EXE%" -c "import PyInstaller" >nul 2>&1
if errorlevel 1 (
    echo       Instalando PyInstaller...
    %PIP_CMD% install pyinstaller --quiet
    if errorlevel 1 (
        echo [ERROR] No se pudo instalar PyInstaller.
        pause
        exit /b 1
    )
)
echo       OK

REM ── Instalar dependencias ─────────────────────────────────────────────────
echo [2/4] Instalando dependencias...
%PIP_CMD% install -r requirements.txt --quiet
if errorlevel 1 (
    echo [ERROR] Fallo al instalar dependencias.
    pause
    exit /b 1
)
echo       OK

REM ── Limpiar builds anteriores ────────────────────────────────────────────
echo [3/4] Limpiando builds anteriores...
if exist "dist\PhotoVault.exe" del /f /q "dist\PhotoVault.exe"
if exist "build" rmdir /s /q "build"
echo       OK

REM ── Compilar ─────────────────────────────────────────────────────────────
echo [4/4] Compilando (puede tardar 1-3 minutos)...
echo.
"%PYTHON_EXE%" -m PyInstaller PhotoVault.spec --noconfirm

if errorlevel 1 (
    echo.
    echo [ERROR] La compilacion fallo. Revisa los mensajes de arriba.
    pause
    exit /b 1
)

REM ── Resultado ────────────────────────────────────────────────────────────
echo.
echo  ====================================================
echo   Listo!
echo   Ejecutable: dist\PhotoVault.exe
echo  ====================================================
echo.
explorer dist
pause
