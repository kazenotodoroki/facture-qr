"""Base de données SQLite : entreprise, clients, tarifs, factures."""
import sqlite3
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
DB_PATH = DATA_DIR / "factures.db"
PDF_DIR = DATA_DIR / "pdf"

SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT
);
CREATE TABLE IF NOT EXISTS clients (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    contact TEXT DEFAULT '',
    street TEXT DEFAULT '',
    house_num TEXT DEFAULT '',
    pcode TEXT DEFAULT '',
    city TEXT DEFAULT '',
    country TEXT DEFAULT 'CH',
    email TEXT DEFAULT '',
    cc_email TEXT DEFAULT '',
    notes TEXT DEFAULT '',
    archived INTEGER DEFAULT 0,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS rates (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    client_id INTEGER NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    label TEXT NOT NULL,
    rate REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS invoices (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    number TEXT UNIQUE NOT NULL,
    client_id INTEGER NOT NULL REFERENCES clients(id),
    client_snapshot TEXT,
    issue_date TEXT NOT NULL,
    due_date TEXT NOT NULL,
    event_name TEXT DEFAULT '',
    event_date TEXT DEFAULT '',
    event_place TEXT DEFAULT '',
    mode TEXT NOT NULL CHECK(mode IN ('hours','flat')),
    subtotal REAL NOT NULL,
    vat_rate REAL DEFAULT 0,
    vat_amount REAL DEFAULT 0,
    rounding REAL DEFAULT 0,
    total REAL NOT NULL,
    reference TEXT DEFAULT '',
    notes TEXT DEFAULT '',
    status TEXT DEFAULT 'brouillon',
    pdf_path TEXT DEFAULT '',
    sent_at TEXT DEFAULT '',
    sent_to TEXT DEFAULT '',
    paid_at TEXT DEFAULT '',
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS invoice_lines (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    invoice_id INTEGER NOT NULL REFERENCES invoices(id) ON DELETE CASCADE,
    position INTEGER DEFAULT 0,
    line_date TEXT DEFAULT '',
    description TEXT DEFAULT '',
    rate_label TEXT DEFAULT '',
    quantity REAL DEFAULT 1,
    unit_price REAL DEFAULT 0,
    amount REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_inv_client ON invoices(client_id);
CREATE INDEX IF NOT EXISTS idx_inv_date ON invoices(issue_date);
"""

DEFAULT_SETTINGS = {
    # Entreprise
    "company_name": "", "company_street": "", "company_house_num": "",
    "company_pcode": "", "company_city": "", "company_country": "CH",
    "company_email": "", "company_phone": "", "company_website": "",
    "company_iban": "", "company_vat_number": "", "company_logo": "",
    "vat_rate": "0", "payment_days": "30", "invoice_prefix": "",
    "invoice_numbering": "sequential", "random_digits": "6", "random_with_year": "1",
    # Devis
    "quote_prefix": "D-", "quote_validity_days": "30", "quote_show_acceptance": "1",
    "quote_terms_text": "Devis valable jusqu'au {validite}. Les heures sont estimées et seront facturées selon le temps effectif.",
    "mail_quote_subject": "Devis {numero} – {entreprise}",
    "mail_quote_message": "Bonjour,\n\nVous trouverez en pièce jointe le devis {numero}{evenement}, valable jusqu'au {validite}.\nNous restons à votre disposition pour toute question et vous adressons nos meilleures salutations.",
    "round_5ct": "1",
    "show_vat": "0",
    "show_payment_terms": "1",
    "payment_terms_text": "Payable à {jours} jours, d'ici au {echeance}, au moyen du bulletin QR ci-dessous.",
    "default_timesheet": "1",
    "default_summary_mode": "lines",
    "show_due_date": "1",
    "invoice_footer": "Merci pour votre confiance.",
    # Design
    "pdf_accent": "#1F3A5F",
    "mail_primary": "#1F3A5F", "mail_accent": "#E9B44C",
    "mail_background": "#F4F1EC", "mail_card": "#FFFFFF", "mail_text": "#1D1D1F",
    "mail_subject": "Facture {numero} – {entreprise}",
    "mail_message": "Bonjour,\n\nVous trouverez en pièce jointe la facture {numero}{evenement}.\nEn vous remerciant pour votre confiance, nous vous adressons nos meilleures salutations.",
    "mail_show_summary": "1",
    "mail_signature_extra": "",
    # SMTP
    "smtp_host": "", "smtp_port": "587", "smtp_security": "starttls",
    "smtp_user": "", "smtp_password": "", "smtp_from_name": "",
    "smtp_bcc_self": "1",
    # Sauvegarde
    "backup_dir": "", "backup_keep": "30",
}


def connect():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


MIGRATIONS = {
    "invoices": {"show_vat": "INTEGER DEFAULT 0", "show_terms": "INTEGER DEFAULT 1",
                 "show_timesheet": "INTEGER DEFAULT 0", "summary_mode": "TEXT DEFAULT 'lines'",
                 "show_due": "INTEGER DEFAULT 1",
                 "doc_type": "TEXT DEFAULT 'invoice'", "valid_until": "TEXT DEFAULT ''",
                 "quote_id": "INTEGER", "converted_invoice_id": "INTEGER"},
    "invoice_lines": {"start_time": "TEXT DEFAULT ''", "end_time": "TEXT DEFAULT ''",
                      "kind": "TEXT DEFAULT 'hours'"},
}


def init_db():
    PDF_DIR.mkdir(parents=True, exist_ok=True)
    with connect() as conn:
        conn.executescript(SCHEMA)
        for table, cols in MIGRATIONS.items():
            have = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
            for col, decl in cols.items():
                if col not in have:
                    conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")
                    if table == "invoices" and col == "show_vat":
                        conn.execute("UPDATE invoices SET show_vat = CASE WHEN vat_rate > 0 THEN 1 ELSE 0 END")
                    if table == "invoice_lines" and col == "kind":
                        # anciens forfaits : le libellé passe dans rate_label, comme les nouvelles lignes
                        conn.execute("""UPDATE invoice_lines SET kind='flat', rate_label=description, description=''
                                        WHERE invoice_id IN (SELECT id FROM invoices WHERE mode='flat')""")
        for k, v in DEFAULT_SETTINGS.items():
            conn.execute("INSERT OR IGNORE INTO settings(key, value) VALUES (?, ?)", (k, v))


def get_settings():
    with connect() as conn:
        rows = conn.execute("SELECT key, value FROM settings").fetchall()
    s = dict(DEFAULT_SETTINGS)
    s.update({r["key"]: r["value"] for r in rows})
    return s


def save_settings(values: dict):
    with connect() as conn:
        for k, v in values.items():
            conn.execute("INSERT INTO settings(key, value) VALUES (?, ?) "
                         "ON CONFLICT(key) DO UPDATE SET value = excluded.value", (k, v))


def doc_prefix(settings: dict, doc_type: str = "invoice") -> str:
    if doc_type == "quote":
        return settings.get("quote_prefix") or "D-"
    return settings.get("invoice_prefix", "")


def next_invoice_number(conn, year: int, prefix: str = "", doc_type: str = "invoice") -> str:
    """Numérotation annuelle : 2026-001, 2026-002… (préfixe optionnel), séparée factures / devis."""
    pattern = f"{prefix}{year}-%"
    rows = conn.execute("SELECT number FROM invoices WHERE number LIKE ? AND COALESCE(doc_type,'invoice')=?",
                        (pattern, doc_type)).fetchall()
    n = 0
    for r in rows:
        try:
            n = max(n, int(r["number"].rsplit("-", 1)[1]))
        except (ValueError, IndexError):
            pass
    return f"{prefix}{year}-{n + 1:03d}"


def number_exists(conn, number: str, exclude_id=None) -> bool:
    row = conn.execute("SELECT id FROM invoices WHERE number=? AND id IS NOT ?", (number, exclude_id)).fetchone()
    return row is not None


def new_invoice_number(conn, year: int, settings: dict, doc_type: str = "invoice") -> str:
    """Numéro automatique selon le réglage : séquentiel (2026-001) ou aléatoire (2026-483920)."""
    prefix = doc_prefix(settings, doc_type)
    if settings.get("invoice_numbering") != "random":
        n = next_invoice_number(conn, year, prefix, doc_type)
        while number_exists(conn, n):  # garde-fou si deux préfixes identiques
            head, num = n.rsplit("-", 1)
            n = f"{head}-{int(num) + 1:03d}"
        return n
    import secrets
    try:
        digits = min(max(int(settings.get("random_digits") or 6), 4), 9)
    except ValueError:
        digits = 6
    head = f"{prefix}{year}-" if settings.get("random_with_year", "1") == "1" else prefix
    for _ in range(200):
        n = str(secrets.randbelow(9 * 10 ** (digits - 1)) + 10 ** (digits - 1))  # pas de 0 en tête
        if not number_exists(conn, head + n):
            return head + n
    raise RuntimeError("Impossible de générer un numéro unique : augmentez le nombre de chiffres.")
