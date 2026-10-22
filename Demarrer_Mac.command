#!/bin/bash
cd "$(dirname "$0")"
echo "Installation / verification des dependances..."
python3 -m pip install -q -r requirements.txt 2>/dev/null || python3 -m pip install -q --user -r requirements.txt
echo
echo "FactureQR demarre sur http://127.0.0.1:5000  (laissez cette fenetre ouverte)"
python3 app.py
