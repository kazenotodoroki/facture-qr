"""Sauvegarde automatique vers un dossier synchronisé (Google Drive, iCloud, OneDrive, Dropbox…).

Le plus simple et le plus fiable : installer « Google Drive pour ordinateur »
et indiquer ici un dossier du Drive, par ex. « G:\\Mon Drive\\Factures » (Windows)
ou « ~/Library/CloudStorage/GoogleDrive-…/Mon Drive/Factures » (Mac).
Le logiciel y copie la base de données et tous les PDF après chaque modification.
"""
import shutil
import sqlite3
import threading
from datetime import datetime
from pathlib import Path

from db import DB_PATH, PDF_DIR

_lock = threading.Lock()
last_status = {"ok": None, "when": "", "message": "Jamais sauvegardé"}


def run_backup(settings) -> dict:
    target = (settings.get("backup_dir") or "").strip()
    if not target:
        last_status.update(ok=None, message="Aucun dossier de sauvegarde configuré")
        return last_status
    with _lock:
        try:
            root = Path(target).expanduser()
            root.mkdir(parents=True, exist_ok=True)
            # 1) base de données (copie cohérente même en cours d'utilisation)
            src = sqlite3.connect(DB_PATH)
            dst = sqlite3.connect(root / "factures.db")
            with dst:
                src.backup(dst)
            src.close()
            dst.close()
            # 2) instantané quotidien, on garde les N derniers
            snaps = root / "historique"
            snaps.mkdir(exist_ok=True)
            today = snaps / f"factures_{datetime.now():%Y-%m-%d}.db"
            shutil.copy2(root / "factures.db", today)
            keep = int(settings.get("backup_keep") or 30)
            for old in sorted(snaps.glob("factures_*.db"))[:-keep]:
                old.unlink(missing_ok=True)
            # 3) PDF (copie uniquement des nouveaux / modifiés)
            n = 0
            for pdf in PDF_DIR.rglob("*.pdf"):
                rel = pdf.relative_to(PDF_DIR)
                out = root / "PDF" / rel
                if not out.exists() or out.stat().st_mtime < pdf.stat().st_mtime:
                    out.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(pdf, out)
                    n += 1
            # 4) PDF des factures supprimées : on les retire aussi de la sauvegarde
            removed = 0
            if (root / "PDF").exists():
                for out in (root / "PDF").rglob("*.pdf"):
                    if not (PDF_DIR / out.relative_to(root / "PDF")).exists():
                        out.unlink(missing_ok=True)
                        removed += 1
            extra = f", {removed} retirés" if removed else ""
            last_status.update(ok=True, when=f"{datetime.now():%d.%m.%Y %H:%M}",
                               message=f"Sauvegardé dans {root} ({n} PDF copiés{extra})")
        except Exception as exc:  # on n'empêche jamais l'utilisation du logiciel
            last_status.update(ok=False, when=f"{datetime.now():%d.%m.%Y %H:%M}",
                               message=f"Échec de la sauvegarde : {exc}")
    return last_status


def backup_async(settings):
    threading.Thread(target=run_backup, args=(dict(settings),), daemon=True).start()
