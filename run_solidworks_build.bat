@echo off
rem ASTraCAD - real SOLIDWORKS run through the adapter. Needs SOLIDWORKS open.
rem Creates two NEW parts in the live session (closed afterwards) and writes files to _win\sw2 only.
cd /d "%~dp0"
if not exist _win\sw2 mkdir _win\sw2
set PYTHONIOENCODING=utf-8
set PYTHONPATH=src
py -m astracad_cad --db _win\tasks.sqlite3 tasks --recover > _win\sw2\recover.log 2>&1
py -m astracad_cad --db _win\tasks.sqlite3 status > _win\sw2\status.log 2>&1
py -m astracad_cad --db _win\tasks.sqlite3 build "Create a 60 x 40 x 20 mm block in SolidWorks and export STEP" --out _win\sw2 --authorize "KM" > _win\sw2\block.log 2>&1
echo exit=%ERRORLEVEL% >> _win\sw2\block.log
py -m astracad_cad --db _win\tasks.sqlite3 build "SolidWorks shaft diameter 25 mm length 100 mm, export STEP" --out _win\sw2 --authorize "KM" > _win\sw2\shaft.log 2>&1
echo exit=%ERRORLEVEL% >> _win\sw2\shaft.log
py -m astracad_cad --db _win\tasks.sqlite3 build "Create a 60 x 40 x 20 mm block in SolidWorks with a 10 mm hole at the centre, export STEP" --out _win\sw2 --authorize "KM" > _win\sw2\hole.log 2>&1
echo exit=%ERRORLEVEL% >> _win\sw2\hole.log
py -m pytest -q -p no:cacheprovider > _win\sw2\pytest.log 2>&1
echo exit=%ERRORLEVEL% >> _win\sw2\pytest.log
py -m astracad_cad --db _win\tasks.sqlite3 tasks > _win\sw2\tasks.log 2>&1
echo done %DATE% %TIME% > _win\sw2\done.txt
