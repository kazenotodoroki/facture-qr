@echo off
cd /d "%~dp0"
echo Installation / verification des dependances...
python -m pip install -q -r requirements.txt
echo.
echo FactureQR demarre sur http://127.0.0.1:5000  (laissez cette fenetre ouverte)
python app.py
pause
