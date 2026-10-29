"""Inventaire de location : disponibilité du matériel selon les factures et devis."""
from datetime import date

# Une ligne compte comme réservation si elle a un article et une date de début, et si son document est :
#  - une facture non annulée → ferme
#  - un devis accepté (pas encore transformé en facture) → ferme
#  - un devis brouillon / envoyé encore valable → option
_BASE = """
SELECT l.id AS line_id, l.item_id, l.quantity, l.rent_from,
       COALESCE(NULLIF(l.rent_to, ''), l.rent_from) AS rent_to,
       i.id AS doc_id, i.number, i.doc_type, i.status, i.event_name, c.name AS client_name,
       CASE WHEN i.doc_type = 'invoice' OR i.status = 'accepté' THEN 1 ELSE 0 END AS firm
FROM invoice_lines l
JOIN invoices i ON i.id = l.invoice_id
JOIN clients c ON c.id = i.client_id
WHERE l.item_id IS NOT NULL AND l.rent_from != ''
  AND ((i.doc_type = 'invoice' AND i.status != 'annulée')
       OR (i.doc_type = 'quote' AND i.converted_invoice_id IS NULL
           AND (i.status = 'accepté' OR (i.status IN ('brouillon', 'envoyé') AND i.valid_until >= :today))))
"""


def bookings(conn, item_id=None, d1=None, d2=None, exclude_doc=None):
    sql = _BASE
    p = {"today": date.today().isoformat()}
    if item_id is not None:
        sql += " AND l.item_id = :item"
        p["item"] = item_id
    if d1 and d2:
        sql += " AND l.rent_from <= :d2 AND COALESCE(NULLIF(l.rent_to, ''), l.rent_from) >= :d1"
        p.update(d1=d1, d2=d2)
    if exclude_doc:
        sql += " AND i.id != :ex"
        p["ex"] = exclude_doc
    return conn.execute(sql + " ORDER BY l.rent_from", p).fetchall()


def availability(conn, d1, d2, exclude_doc=None):
    """{item_id: {"firm": n, "option": n}} sur la période (cumul prudent des réservations qui se chevauchent)."""
    out = {}
    for b in bookings(conn, None, d1, d2, exclude_doc):
        a = out.setdefault(b["item_id"], {"firm": 0.0, "option": 0.0})
        a["firm" if b["firm"] else "option"] += b["quantity"] or 0
    return out
