@echo off
rem ASTraCAD - rebuild mac, then edit COPIES of two existing parts in SOLIDWORKS (originals untouched).
cd /d "%~dp0"
if not exist _win\edit2 mkdir _win\edit2
set PYTHONIOENCODING=utf-8
set PYTHONPATH=src
dotnet build ..\mecagent-alternative\src\Mac\Mac.csproj -c Release > _win\edit2\build.log 2>&1
echo exit=%ERRORLEVEL% >> _win\edit2\build.log
py -m astracad_cad --db _win\tasks.sqlite3 inspect _win\sw\block_60x40x20.SLDPRT > _win\edit2\inspect.log 2>&1
echo exit=%ERRORLEVEL% >> _win\edit2\inspect.log
py -m astracad_cad --db _win\tasks.sqlite3 edit _win\sw\block_60x40x20.SLDPRT --set D1@Boss-Extrude1=35 --name block_depth35 --out _win\edit2 --authorize KM --expect-size 60 40 35 --expect-volume 84000 > _win\edit2\block.log 2>&1
echo exit=%ERRORLEVEL% >> _win\edit2\block.log
py -m astracad_cad --db _win\tasks.sqlite3 edit _win\sw\shaft_d25_l100.SLDPRT --set D1@Boss-Extrude1=120 --name shaft_l120 --out _win\edit2 --authorize KM --expect-size 25 25 120 > _win\edit2\shaft.log 2>&1
echo exit=%ERRORLEVEL% >> _win\edit2\shaft.log
py -m astracad_cad --db _win\tasks.sqlite3 edit _win\sw\block_60x40x20.SLDPRT --set D9@NoSuchFeature=5 --name block_bad --out _win\edit2 --authorize KM > _win\edit2\bad.log 2>&1
echo exit=%ERRORLEVEL% >> _win\edit2\bad.log
py -m pytest -q -p no:cacheprovider > _win\edit2\pytest.log 2>&1
echo exit=%ERRORLEVEL% >> _win\edit2\pytest.log
echo done %DATE% %TIME% > _win\edit2\done.txt
