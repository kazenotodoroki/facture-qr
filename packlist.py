"""PDF de logistique : liste de chargement (départ du matériel) et bon de retour."""
import re
from pathlib import Path

from reportlab.lib.colors import HexColor
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader

from pdfgen import (HAIR, INK, MUTED, PAGE_H, PAGE_W, X0, X1, _ellipsis, _mix, _NumberedCanvas,
                    _register_fonts, fdate, fnum)

ROW = 27
SEC = 20
BOTTOM = 24 * mm
SIGN_H = 34 * mm

MODES = {
    "chargement": {
        "title": "Liste de chargement",
        "date_label": "Départ",
        "signs": ("Préparé par", "Chargé / livré par", "Reçu par le client"),
    },
    "retour": {
        "title": "Bon de retour",
        "date_label": "Retour prévu",
        "signs": ("Rendu par", "Contrôlé par", "Date du retour"),
    },
}


def rental_lines(lines):
    """Lignes de matériel : article de l'inventaire ou ligne de forfait avec dates de location."""
    return [l for l in lines if l.get("kind") == "flat" and (l.get("item_id") or l.get("rent_from"))]


def _period(lines):
    starts = [l["rent_from"] for l in lines if l.get("rent_from")]
    ends = [l.get("rent_to") or l["rent_from"] for l in lines if l.get("rent_from")]
    return (min(starts) if starts else ""), (max(ends) if ends else "")


def _fper(a, b):
    if not a:
        return "—"
    if not b or a == b:
        return fdate(a)
    return f"{fdate(a)[:5]} → {fdate(b)}" if a[:4] == b[:4] else f"{fdate(a)} → {fdate(b)}"


def generate_packlist(out, settings, groups, mode="chargement", subtitle=""):
    """groups : liste de dict(doc, client, lines, items) ; un groupe = un document (facture ou devis).
    `out` : chemin ou objet fichier (BytesIO)."""
    _register_fonts()
    M = MODES[mode]
    acc = HexColor(settings.get("pdf_accent") or "#1F3A5F")
    soft = _mix(settings.get("pdf_accent") or "#1F3A5F", 0.92)
    c = _NumberedCanvas(out, pagesize=A4)
    c.setTitle(M["title"])
    c.setAuthor(settings.get("company_name", ""))

    # colonnes
    if mode == "chargement":
        cols = [("Prép.", X0, "c"), ("Chargé", X0 + 12 * mm, "c"), ("Qté", X0 + 31 * mm, "r"),
                ("Article", X0 + 35 * mm, "l"), ("Période", X0 + 112 * mm, "l"), ("Remarques", X0 + 140 * mm, "l")]
        art_w = 74 * mm
    else:
        cols = [("Sortie", X0 + 10 * mm, "r"), ("Article", X0 + 15 * mm, "l"), ("Période", X0 + 80 * mm, "l"),
                ("Rentré", X0 + 113 * mm, "c"), ("OK", X0 + 127 * mm, "c"), ("État / remarques", X0 + 135 * mm, "l")]
        art_w = 62 * mm
    art_x = [x for lab, x, _ in cols if lab == "Article"][0]
    per_x = [x for lab, x, _ in cols if lab == "Période"][0]
    rem_x = cols[-1][1]

    def box(x, y, s=3.6 * mm):
        c.setStrokeColor(INK)
        c.setLineWidth(0.7)
        c.rect(x, y, s, s, stroke=1, fill=0)

    def band(sub):
        c.setFillColor(acc)
        c.rect(0, PAGE_H - 3.5 * mm, PAGE_W, 3.5 * mm, stroke=0, fill=1)
        top = PAGE_H - 18 * mm
        logo = settings.get("company_logo")
        drew = False
        if logo and Path(logo).exists():
            try:
                img = ImageReader(logo)
                iw, ih = img.getSize()
                h = 11 * mm
                w = min(iw * h / ih, 50 * mm)
                h = w * ih / iw
                c.drawImage(img, X0, top - h + 4 * mm, w, h, mask="auto")
                drew = True
            except Exception:
                pass
        if not drew:
            c.setFillColor(INK)
            c.setFont("Inter-SemiBold", 13)
            c.drawString(X0, top, settings.get("company_name", ""))
        c.setFillColor(acc)
        c.setFont("Inter-Light", 22)
        c.drawRightString(X1, top - 2, M["title"])
        c.setFillColor(MUTED)
        c.setFont("Inter-Regular", 9)
        c.drawRightString(X1, top - 15, sub)
        return PAGE_H - 34 * mm

    def table_header(y):
        c.setFont("Inter-Medium", 7)
        c.setFillColor(MUTED)
        for lab, x, al in cols:
            if al == "r":
                c.drawRightString(x, y, lab.upper())
            elif al == "c":
                c.drawCentredString(x + 1.8 * mm, y, lab.upper())
            else:
                c.drawString(x, y, lab.upper())
        c.setStrokeColor(INK)
        c.setLineWidth(0.6)
        c.line(X0, y - 5, X1, y - 5)
        return y - 7

    def info_block(y, g):
        doc, cl = g["doc"], g["client"]
        a, b = _period(g["lines"])
        left = [("CLIENT", cl.get("name", "")),
                ("", cl.get("contact", "")),
                ("", f"{cl.get('street', '')} {cl.get('house_num', '')}".strip()),
                ("", f"{cl.get('pcode', '')} {cl.get('city', '')}".strip()),
                ("", cl.get("email", ""))]
        word = "Devis" if doc.get("doc_type") == "quote" else "Facture"
        right = []
        if doc.get("event_name"):
            right.append(("ÉVÉNEMENT", doc["event_name"]))
            ev = " · ".join(v for v in (fdate(doc.get("event_date", "")), doc.get("event_place", "")) if v)
            if ev:
                right.append(("", ev))
        right.append(("DOCUMENT", f"{word} N° {doc['number']}"))
        right.append((M["date_label"].upper(), fdate(a if mode == "chargement" else b) or "—"))
        right.append(("PÉRIODE DE LOCATION", _fper(a, b)))
        bh = 31 * mm
        c.setFillColor(soft)
        c.rect(X0, y - bh, X1 - X0, bh, stroke=0, fill=1)
        c.setFillColor(acc)
        c.rect(X0, y - bh, 1.1 * mm, bh, stroke=0, fill=1)
        ly = y - 6 * mm
        for lab, val in left:
            if not val:
                continue
            if lab:
                c.setFont("Inter-Medium", 6.8)
                c.setFillColor(acc)
                c.drawString(X0 + 5 * mm, ly, lab)
                ly -= 11
                c.setFont("Inter-SemiBold", 10.5)
                c.setFillColor(INK)
            else:
                c.setFont("Inter-Regular", 9)
                c.setFillColor(INK)
            c.drawString(X0 + 5 * mm, ly, _ellipsis(val, "Inter-Regular", 9.5, 75 * mm))
            ly -= 11.5
        rx = X0 + 86 * mm
        ry = y - 6 * mm
        for lab, val in right:
            c.setFont("Inter-Medium", 6.8)
            c.setFillColor(acc)
            c.drawString(rx, ry, lab)
            c.setFont("Inter-SemiBold" if lab == "ÉVÉNEMENT" else "Inter-Regular", 9.5)
            c.setFillColor(INK)
            c.drawString(rx + 30 * mm, ry, _ellipsis(val, "Inter-Regular", 9.5, X1 - rx - 33 * mm))
            ry -= 12.5
        return y - bh - 7 * mm

    def draw_row(y, l, it):
        name = l.get("rate_label") or (it or {}).get("name", "")
        sub = " · ".join(v for v in ((it or {}).get("reference", ""), l.get("description", ""),
                                     (it or {}).get("notes", "")) if v)
        base = y - (11 if sub else 16)
        q = fnum(round(l.get("quantity") or 0, 2))
        c.setFillColor(INK)
        if mode == "chargement":
            box(cols[0][1], y - 18, 4 * mm)
            box(cols[1][1] + 1 * mm, y - 18, 4 * mm)
            c.setFont("Inter-SemiBold", 10.5)
            c.drawRightString(cols[2][1], base, q)
        else:
            c.setFont("Inter-SemiBold", 10.5)
            c.drawRightString(cols[0][1], base, q)
            # quantité rentrée : case à remplir
            c.setStrokeColor(INK)
            c.setLineWidth(0.6)
            c.rect(cols[3][1] - 2.5 * mm, y - 19, 9 * mm, 6 * mm, stroke=1, fill=0)
            box(cols[4][1], y - 18, 4 * mm)
        c.setFont("Inter-Medium", 9.5)
        c.drawString(art_x, base, _ellipsis(name, "Inter-Medium", 9.5, art_w))
        if sub:
            c.setFont("Inter-Regular", 8)
            c.setFillColor(MUTED)
            c.drawString(art_x, base - 10.5, _ellipsis(sub, "Inter-Regular", 8, art_w))
        c.setFont("Inter-Regular", 8.5)
        c.setFillColor(INK)
        c.drawString(per_x, base, _fper(l.get("rent_from", ""), l.get("rent_to", "")))
        # ligne à remplir à la main
        c.setStrokeColor(HAIR)
        c.setLineWidth(0.6)
        c.line(rem_x, y - 18, X1, y - 18)
        c.setStrokeColor(HAIR)
        c.setLineWidth(0.5)
        c.line(X0, y - ROW, X1, y - ROW)
        return y - ROW

    def section(y, label):
        c.setFont("Inter-SemiBold", 7.5)
        c.setFillColor(acc)
        c.drawString(X0, y - SEC + 6, label.upper())
        return y - SEC

    def footer_block(y, g):
        # total + remarques + signatures, en bas de page
        n_art = len(g["lines"])
        n_pcs = sum(l.get("quantity") or 0 for l in g["lines"])
        c.setFont("Inter-Medium", 9.5)
        c.setFillColor(INK)
        y -= 14
        c.drawString(X0, y - 4, f"Total : {fnum(round(n_pcs, 2))} pièce{'s' if n_pcs > 1 else ''} · "
                                f"{n_art} ligne{'s' if n_art > 1 else ''}")
        # remarques
        top = BOTTOM + SIGN_H
        ry = y - 22
        if ry - 10 > top + 8:
            c.setFont("Inter-Medium", 7)
            c.setFillColor(MUTED)
            c.drawString(X0, ry, "REMARQUES")
            ry -= 16
            c.setStrokeColor(HAIR)
            c.setLineWidth(0.6)
            k = 0
            while ry > top + 6 and k < 4:
                c.line(X0, ry, X1, ry)
                ry -= 16
                k += 1
        # signatures
        sy = BOTTOM
        w = (X1 - X0 - 16 * mm) / 3
        c.setStrokeColor(HAIR)
        c.setLineWidth(0.6)
        c.line(X0, sy + SIGN_H - 6 * mm, X1, sy + SIGN_H - 6 * mm)
        for k, lab in enumerate(M["signs"]):
            x = X0 + k * (w + 8 * mm)
            c.setFont("Inter-Medium", 7)
            c.setFillColor(MUTED)
            c.drawString(x, sy + SIGN_H - 12 * mm, lab.upper())
            c.setStrokeColor(INK)
            c.setLineWidth(0.5)
            c.line(x, sy + 9 * mm, x + w, sy + 9 * mm)
            c.setFont("Inter-Regular", 7.5)
            c.setFillColor(MUTED)
            c.drawString(x, sy + 5 * mm, "Date et signature" if k < 2 or mode == "chargement" else "Date et heure")

    for gi, g in enumerate(groups):
        doc = g["doc"]
        items = g.get("items", {})
        sub = subtitle or f"{'Devis' if doc.get('doc_type') == 'quote' else 'Facture'} N° {doc['number']}"
        y = band(sub)
        y = info_block(y, g)
        # tri par catégorie puis par nom
        rows = []
        for l in g["lines"]:
            it = items.get(l.get("item_id")) if l.get("item_id") else None
            rows.append(((it or {}).get("category") or "Divers", l, it))
        nat = lambda t: [int(p) if p.isdigit() else p for p in re.split(r"(\d+)", (t or "").lower())]
        rows.sort(key=lambda r: (r[0].lower() == "divers", r[0].lower(), nat(r[1].get("rate_label"))))
        cats = []
        for cat, l, it in rows:
            if not cats or cats[-1][0] != cat:
                cats.append((cat, []))
            cats[-1][1].append((l, it))
        show_cats = len(cats) > 1 or (cats and cats[0][0] != "Divers")

        y = table_header(y)
        last_min = BOTTOM + SIGN_H + 24       # place minimale pour le pied (total + signatures)
        n_left = len(rows)
        for cat, entries in cats:
            if show_cats:
                if y - SEC - ROW < BOTTOM + 6 * mm:
                    c.showPage()
                    band(sub + " · suite")
                    y = table_header(PAGE_H - 40 * mm)
                y = section(y, cat)
            for l, it in entries:
                limit = last_min if n_left <= 2 else BOTTOM + 6 * mm
                if y - ROW < limit:
                    c.showPage()
                    band(sub + " · suite")
                    y = table_header(PAGE_H - 40 * mm)
                    if show_cats:
                        y = section(y, cat + " (suite)")
                y = draw_row(y, l, it)
                n_left -= 1
        if y - 38 < BOTTOM + SIGN_H:
            c.showPage()
            band(sub + " · suite")
            y = PAGE_H - 40 * mm
        footer_block(y, g)
        c.showPage()
    if not groups:
        band(subtitle)
        c.setFont("Inter-Regular", 11)
        c.setFillColor(MUTED)
        c.drawString(X0, PAGE_H - 50 * mm, "Aucun matériel sur cette période.")
        c.showPage()
    c.save()
    return out
