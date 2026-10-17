@echo off
rem ASTraCAD - Windows verification run. Writes logs to _win\ ; changes no source file.
cd /d "%~dp0"
if not exist _win mkdir _win
set PYTHONIOENCODING=utf-8
set PYTHONPATH=src
echo started %DATE% %TIME% > _win\started.txt
py --version > _win\env.log 2>&1
py -c "import importlib.util as u;[print(m, bool(u.find_spec(m))) for m in ('pymupdf','pypdfium2','ezdxf','pytest')]" >> _win\env.log 2>&1
py -m pytest -q -p no:cacheprovider > _win\pytest.log 2>&1
echo exit=%ERRORLEVEL% >> _win\pytest.log
py -m astracad_inspect selftest > _win\selftest.log 2>&1
echo exit=%ERRORLEVEL% >> _win\selftest.log
py -m astracad_inspect run tests\fixtures\plate_synthetic.dxf --out _win\inspect > _win\inspect.log 2>&1
echo exit=%ERRORLEVEL% >> _win\inspect.log
py -m astracad_cad --db _win\tasks.sqlite3 status > _win\cad_status.log 2>&1
echo exit=%ERRORLEVEL% >> _win\cad_status.log
echo done %DATE% %TIME% > _win\done.txt
