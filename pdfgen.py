"""Génération du PDF : facture élégante + bulletin QR suisse (ISO 20022 / SIX)."""
import io
import json
import re
from datetime import date
from pathlib import Path

from qrbill import QRBill
from reportlab.lib.colors import HexColor, Color
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas
from reportlab.graphics import renderPDF
from svglib.svglib import svg2rlg

from references import format_reference

FONT_DIR = Path(__file__).resolve().parent / "fonts"
_fonts_ok = False


def _register_fonts():
    global _fonts_ok
    if _fonts_ok:
        return
    for name in ("Light", "Regular", "Medium", "SemiBold"):
        pdfmetrics.registerFont(TTFont(f"Inter-{name}", str(FONT_DIR / f"Inter-{name}.ttf")))
    _fonts_ok = True


# ---------- helpers de format ----------
def chf(v: float) -> str:
    s = f"{abs(v):,.2f}".replace(",", "'")
    return ("-" if v < 0 else "") + s


def fdate(iso: str) -> str:
    if not iso:
        return ""
    try:
        return date.fromisoformat(iso).strftime("%d.%m.%Y")
    except ValueError:
        return iso


def fnum(v: float) -> str:
    return (f"{v:.2f}").rstrip("0").rstrip(".") if v != int(v) else str(int(v))


def _wrap(text, font, size, width):
    lines = []
    for para in (text or "").split("\n"):
        words, cur = para.split(" "), ""
        for w in words:
            test = (cur + " " + w).strip()
            if pdfmetrics.stringWidth(test, font, size) <= width:
                cur = test
            else:
                if cur:
                    lines.append(cur)
                cur = w
        lines.append(cur)
    return lines or [""]


def _mix(hex_color, t):
    """Mélange une couleur avec du blanc (t = 0 → couleur, 1 → blanc)."""
    c = HexColor(hex_color)
    return Color(c.red + (1 - c.red) * t, c.green + (1 - c.green) * t, c.blue + (1 - c.blue) * t)


# ---------- QR-bill ----------
def build_qr_drawing(settings, client, invoice):
    creditor = {
        "name": settings["company_name"], "street": settings["company_street"],
        "house_num": settings["company_house_num"], "pcode": settings["company_pcode"],
        "city": settings["company_city"], "country": settings["company_country"] or "CH",
    }
    debtor = {
        "name": client["name"], "street": client.get("street", ""),
        "house_num": client.get("house_num", ""), "pcode": client.get("pcode", ""),
        "city": client.get("city", ""), "country": client.get("country") or "CH",
    }
    info = f"Facture {invoice['number']}"
    if invoice.get("event_name"):
        info += f" – {invoice['event_name']}"
    bill = QRBill(
        account=settings["company_iban"],
        creditor=creditor,
        debtor=debtor if debtor["pcode"] and debtor["city"] else None,
        amount=f"{invoice['total']:.2f}",
        currency="CHF",
        reference_number=invoice.get("reference") or None,
        additional_information=info[:140],
        language="fr",
        top_line=True,
        payment_line=True,
    )
    buf = io.StringIO()
    bill.as_svg(buf)
    drawing = svg2rlg(io.BytesIO(buf.getvalue().encode("utf-8")))
    return drawing


def fpct(v) -> str:
    return fnum(round(float(v or 0), 2))


def _ellipsis(text, font, size, width):
    text = (text or "").replace("\n", " ").strip()
    if pdfmetrics.stringWidth(text, font, size) <= width:
        return text
    while text and pdfmetrics.stringWidth(text + "…", font, size) > width:
        text = text[:-1]
    return text.rstrip() + "…"


def hours_between(start, end):
    """Durée en heures entre deux heures HH:MM (passe minuit si fin < début)."""
    try:
        h1, m1 = map(int, start.split(":"))
        h2, m2 = map(int, end.split(":"))
    except (ValueError, AttributeError):
        return None
    d = (h2 * 60 + m2) - (h1 * 60 + m1)
    if d <= 0:
        d += 24 * 60
    return round(d / 60, 2)


def quote_terms(settings, invoice):
    tpl = settings.get("quote_terms_text") or ""
    try:
        return tpl.format(validite=fdate(invoice.get("valid_until", "")), jours=settings.get("payment_days", "30"))
    except (KeyError, IndexError, ValueError):
        return tpl


def payment_terms(settings, invoice):
    tpl = settings.get("payment_terms_text") or ""
    if not invoice.get("show_due", 1):
        # échéance masquée : on retire la mention de date
        tpl = re.sub(r",?\s*(d'ici au|jusqu'au|au plus tard le|avant le)\s*\{echeance\}", "", tpl).replace("{echeance}", "")
    try:
        return tpl.format(jours=settings.get("payment_days", "30"), echeance=fdate(invoice["due_date"]))
    except (KeyError, IndexError, ValueError):
        return tpl


# ---------- mise en page ----------
PAGE_W, PAGE_H = A4
QR_H = 105 * mm
X0, X1 = 20 * mm, PAGE_W - 20 * mm
INK = HexColor("#1D1D1F")
MUTED = HexColor("#6E6E73")
HAIR = HexColor("#E3E3E6")
ROW_H = 27          # hauteur fixe d'une ligne de prestation (pt)
TS_ROW_H = 19       # hauteur d'une ligne du décompte horaire
SEC_H = 22          # hauteur d'un intertitre (Heures / Forfaits et locations)
ROW_H_COMPACT = 23  # variante compacte, utilisée seulement si elle évite une page supplémentaire
SEC_H_COMPACT = 17
CONT_TOP = PAGE_H - 40 * mm


class _NumberedCanvas(canvas.Canvas):
    """Diffère le rendu des pages pour écrire « Page x / N » à la fin."""

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self._pages = []
        self.num_y = 12 * mm

    def showPage(self):
        self._pages.append(dict(self.__dict__))
        self._startPage()
        self.num_y = 12 * mm

    def save(self):
        n = len(self._pages)
        for state in self._pages:
            self.__dict__.update(state)
            if n > 1:
                self.setFont("Inter-Regular", 7.5)
                self.setFillColor(MUTED)
                self.drawRightString(X1, self.num_y, f"Page {self._pageNumber} / {n}")
            canvas.Canvas.showPage(self)
        canvas.Canvas.save(self)


def _group_lines(lines):
    """Regroupe les heures par prestation et tarif."""
    groups = {}
    for l in lines:
        key = (l["rate_label"], round(l["unit_price"], 2))
        g = groups.setdefault(key, {"rate_label": l["rate_label"], "unit_price": l["unit_price"],
                                    "quantity": 0, "amount": 0, "dates": []})
        g["quantity"] += l["quantity"]
        g["amount"] += l["amount"]
        if l.get("line_date"):
            g["dates"].append(l["line_date"])
    out = []
    for g in groups.values():
        ds = sorted(set(g["dates"]))
        if len(ds) > 1:
            detail = f"{len(ds)} jours · du {fdate(ds[0])[:5]} au {fdate(ds[-1])}"
        elif ds:
            detail = fdate(ds[0])
        else:
            detail = ""
        out.append(dict(g, line_date="", description=detail, amount=round(g["amount"], 2)))
    return out


def generate_invoice_pdf(path, settings, client, invoice, lines):
    """Écrit le PDF (facture + bulletin QR + annexe éventuelle) et retourne son chemin."""
    _register_fonts()
    accent = settings.get("pdf_accent") or "#1F3A5F"
    acc = HexColor(accent)
    soft = _mix(accent, 0.92)
    quote = invoice.get("doc_type") == "quote"
    word = "Devis" if quote else "Facture"
    qr = None if quote else build_qr_drawing(settings, client, invoice)

    c = _NumberedCanvas(str(path), pagesize=A4)
    c.setTitle(f"{word} {invoice['number']}")
    c.setAuthor(settings["company_name"])
    c.setSubject(f"{word} {invoice['number']} – {client['name']}")

    default_kind = "flat" if invoice.get("mode") == "flat" else "hours"
    for l in lines:
        l.setdefault("kind", default_kind)
        if not l.get("kind"):
            l["kind"] = default_kind
    h_lines = [l for l in lines if l["kind"] == "hours"]
    f_lines = [l for l in lines if l["kind"] == "flat"]
    hours_mode = bool(h_lines)                     # il y a des heures à facturer
    mixed = bool(h_lines) and bool(f_lines)
    grouped = hours_mode and invoice.get("summary_mode") == "grouped"
    show_vat = bool(invoice.get("show_vat"))
    show_due = bool(invoice.get("show_due", 1))
    annex = hours_mode and bool(invoice.get("show_timesheet")) and any(l.get("line_date") or l.get("start_time") for l in h_lines)
    simple = not hours_mode and all(abs(l["quantity"] - 1) < 1e-9 and abs((l.get("days") or 1) - 1) < 1e-9
                                    for l in f_lines)

    # ---- colonnes du tableau principal ----
    dated = hours_mode and not grouped
    if simple:
        cols = [("Désignation", X0, "l"), ("Montant", X1, "r")]
        desc_x, desc_w = X0, (X1 - 40 * mm) - X0
    else:
        q_lab, p_lab, d_lab = (("Heures", "Tarif", "Prestation") if not f_lines else
                               ("Qté", "Prix", "Désignation") if mixed else ("Qté", "Prix unit.", "Désignation"))
        desc_x = X0 + 24 * mm if dated else X0
        cols = ([("Date", X0, "l")] if dated else []) + [
            (d_lab, desc_x, "l"), (q_lab, X1 - 62 * mm, "r"), (p_lab, X1 - 32 * mm, "r"), ("Montant", X1, "r")]
        desc_w = (X1 - 78 * mm) - desc_x

    # ---- lignes affichées (hauteur fixe) ----
    rows = []   # (type, ligne, titre, détail, hauteur)

    def add_line(l):
        title = l["rate_label"] or l.get("description", "")
        detail = l.get("description", "") if l["rate_label"] else ""
        if l["kind"] == "hours" and not grouped and not annex and l.get("start_time") and l.get("end_time"):
            times = f"{l['start_time']} – {l['end_time']}"
            detail = f"{times} · {detail}" if detail else times
        if l["kind"] == "flat" and l.get("rent_from"):
            a, b = l["rent_from"], l.get("rent_to") or l["rent_from"]
            period = fdate(a) if a == b else (f"du {fdate(a)[:5]} au {fdate(b)}" if a[:4] == b[:4] else f"du {fdate(a)} au {fdate(b)}")
            detail = f"{period} · {detail}" if detail else period
        rows.append(("line", l, _ellipsis(title, "Inter-Medium", 9.5, desc_w),
                     _ellipsis(detail, "Inter-Regular", 8.5, desc_w) if detail else "", ROW_H))

    if h_lines:
        if mixed:
            rows.append(("section", None, "Heures", "", SEC_H))
        for l in (_group_lines(h_lines) if grouped else h_lines):
            l.setdefault("kind", "hours")
            add_line(l)
    if f_lines:
        if mixed:
            rows.append(("section", None, "Forfaits et locations", "", SEC_H))
        for l in f_lines:
            add_line(l)

    # ---- hauteur du bloc final (totaux + modalités + remarque) ----
    tx_l = X1 - 72 * mm
    left_w = tx_l - X0 - 12 * mm
    terms = (quote_terms(settings, invoice) if quote else payment_terms(settings, invoice)) if invoice.get("show_terms") else ""
    terms_l = _wrap(terms, "Inter-Regular", 8.5, left_w) if terms else []
    notes_l = _wrap(invoice.get("notes", ""), "Inter-Regular", 8.5, left_w)[:10] if invoice.get("notes") else []
    n_tot = 1 + (1 if show_vat else 0) + (1 if invoice.get("rounding") else 0)
    right_h = 8 + 15 * n_tot + 10 + 24
    left_h = 8 + (32 if hours_mode else 0) + 11 * len(terms_l) + (4 + 11 * len(notes_l) if notes_l else 0)
    block_h = max(right_h, left_h)
    acceptance = quote and settings.get("quote_show_acceptance", "1") == "1"
    zone_top = QR_H if not quote else (20 * mm + (30 * mm if acceptance else 0))   # bulletin QR ou bon pour accord
    qr_bottom = zone_top + 13 * mm + block_h  # y minimum pour la dernière ligne sur la dernière page
    other_bottom = 26 * mm                   # y minimum sur une page intermédiaire

    # ---------------- éléments répétés ----------------
    def top_band(title, subtitle):
        c.setFillColor(acc)
        c.rect(0, PAGE_H - 3.5 * mm, PAGE_W, 3.5 * mm, stroke=0, fill=1)
        top = PAGE_H - 18 * mm
        base = top - 13
        logo = settings.get("company_logo")
        drew = False
        if logo and Path(logo).exists():
            try:
                img = ImageReader(logo)
                iw, ih = img.getSize()
                h = 12 * mm
                w = min(iw * h / ih, 55 * mm)
                h = w * ih / iw
                c.drawImage(img, X0, top - h + 4 * mm, w, h, mask="auto")
                drew, base = True, top - h - 1 * mm
            except Exception:
                pass
        if not drew:
            c.setFillColor(INK)
            c.setFont("Inter-SemiBold", 14)
            c.drawString(X0, top, settings["company_name"])
        c.setFillColor(acc)
        c.setFont("Inter-Light", 26 if len(title) < 12 else 20)
        c.drawRightString(X1, top - 3, title)
        c.setFillColor(MUTED)
        c.setFont("Inter-Regular", 9)
        c.drawRightString(X1, top - 16, subtitle)
        return base

    def table_header(y):
        c.setFont("Inter-Medium", 7)
        c.setFillColor(MUTED)
        for lab, cx, al in cols:
            (c.drawRightString if al == "r" else c.drawString)(cx, y, lab.upper())
        c.setStrokeColor(INK)
        c.setLineWidth(0.6)
        c.line(X0, y - 5, X1, y - 5)
        return y - 7

    def carry_line(y, label, amount):
        c.setFont("Inter-Medium", 9)
        c.setFillColor(MUTED)
        c.drawString(tx_l, y, label)
        c.drawRightString(X1, y, chf(amount))

    def draw_row(y, l, title, detail):
        """Ligne à hauteur fixe ROW_H, y = haut de la ligne."""
        base = y - (11.5 if detail else 16.5) + (ROW_H - L["row"]) * (0.45 if detail else 0.5)
        c.setFillColor(INK)
        c.setFont("Inter-Regular", 9.5)
        if dated:
            c.drawString(X0, base, fdate(l.get("line_date", "")))
        if not simple:
            q = fnum(round(l["quantity"], 2)) + (" h" if l["kind"] == "hours" else "")
            if l["kind"] == "flat" and (l.get("days") or 1) != 1:
                q = f"{fnum(round(l['quantity'], 2))} × {fnum(round(l['days'], 2))} j"
            c.drawRightString(cols[-3][1], base, q)
            c.drawRightString(cols[-2][1], base, chf(l["unit_price"]))
        c.setFont("Inter-Medium", 9.5)
        c.drawString(desc_x, base, title)
        c.drawRightString(X1, base, chf(l["amount"]))
        if detail:
            c.setFont("Inter-Regular", 8.5)
            c.setFillColor(MUTED)
            c.drawString(desc_x, base - 10.5, detail)
        c.setStrokeColor(HAIR)
        c.setLineWidth(0.5)
        c.line(X0, y - L["row"], X1, y - L["row"])
        return y - L["row"]

    def draw_section(y, label, cont=False):
        c.setFont("Inter-SemiBold", 7.5)
        c.setFillColor(acc)
        c.drawString(X0, y - L["sec"] + 6, (label + (" (suite)" if cont else "")).upper())
        return y - L["sec"]

    # ---------------- page 1 : en-tête ----------------
    y = top_band(word, f"N° {invoice['number']}")
    c.setFont("Inter-Regular", 8.5)
    c.setFillColor(MUTED)
    contact = [settings["company_phone"], settings["company_email"], settings["company_website"]]
    for s in (f"{settings['company_street']} {settings['company_house_num']}".strip(),
              f"{settings['company_pcode']} {settings['company_city']}".strip(),
              " · ".join(v for v in contact if v),
              f"TVA {settings['company_vat_number']}" if show_vat and settings.get("company_vat_number") else ""):
        if s:
            c.drawString(X0, y, s)
            y -= 11

    ax, ay = 118 * mm, PAGE_H - 44 * mm
    c.setFont("Inter-Medium", 7)
    c.setFillColor(MUTED)
    c.drawString(ax, ay + 13, "FACTURÉ À")
    c.setFillColor(INK)
    c.setFont("Inter-SemiBold", 10.5)
    c.drawString(ax, ay, client["name"])
    c.setFont("Inter-Regular", 10)
    yy = ay - 13.5
    for s in (client.get("contact"),
              f"{client.get('street', '')} {client.get('house_num', '')}".strip(),
              f"{client.get('pcode', '')} {client.get('city', '')}".strip(),
              client.get("country") if client.get("country") not in ("", "CH", None) else ""):
        if s:
            c.drawString(ax, yy, s)
            yy -= 13.5

    my = PAGE_H - 62 * mm
    mx = X0
    meta = [("Date", fdate(invoice["issue_date"]))]
    if quote:
        if invoice.get("valid_until"):
            meta.append(("Valable jusqu'au", fdate(invoice["valid_until"])))
    elif show_due:
        meta.append(("Échéance", fdate(invoice["due_date"])))
    for lab, val in meta:
        c.setFont("Inter-Medium", 7)
        c.setFillColor(MUTED)
        c.drawString(mx, my, lab.upper())
        c.setFont("Inter-Regular", 10)
        c.setFillColor(INK)
        c.drawString(mx, my - 13.5, val)
        mx += 30 * mm

    y = min(my - 13.5, yy) - 9 * mm
    if invoice.get("event_name"):
        bh = 12 * mm
        c.setFillColor(soft)
        c.roundRect(X0, y - bh, X1 - X0, bh, 2 * mm, stroke=0, fill=1)
        c.setFillColor(acc)
        c.rect(X0, y - bh, 1.1 * mm, bh, stroke=0, fill=1)
        c.setFont("Inter-Medium", 6.8)
        c.drawString(X0 + 5.5 * mm, y - 4.4 * mm, "ÉVÉNEMENT")
        c.setFillColor(INK)
        c.setFont("Inter-SemiBold", 10.5)
        c.drawString(X0 + 5.5 * mm, y - 9.2 * mm, _ellipsis(invoice["event_name"], "Inter-SemiBold", 10.5, 100 * mm))
        details = " · ".join(v for v in (fdate(invoice.get("event_date", "")), invoice.get("event_place", "")) if v)
        if details:
            c.setFont("Inter-Regular", 9)
            c.setFillColor(MUTED)
            c.drawRightString(X1 - 5 * mm, y - 9.2 * mm, details)
        y -= bh + 8 * mm

    # ---------------- lignes, avec reports ----------------
    y = table_header(y)
    # mise en page compacte (toutes les lignes à la même hauteur réduite) si cela évite une page de plus
    L = {"row": ROW_H, "sec": SEC_H}
    n_line = sum(1 for r in rows if r[0] == "line")
    n_sec = len(rows) - n_line
    full = n_line * ROW_H + n_sec * SEC_H
    compact = n_line * ROW_H_COMPACT + n_sec * SEC_H_COMPACT
    if y - full < qr_bottom <= y - compact:
        L = {"row": ROW_H_COMPACT, "sec": SEC_H_COMPACT}
    rows = [(k, l, t, d, L["row"] if k == "line" else L["sec"]) for k, l, t, d, _ in rows]
    running = 0.0
    n = len(rows)
    i = 0
    section = None
    page_start = True
    while i < n:
        kind, l, t, d, h = rows[i]
        rem_h = sum(r[4] for r in rows[i:])
        rem_lines = sum(1 for r in rows[i:] if r[0] == "line")
        fits_all = y - rem_h >= qr_bottom
        need = h + (rows[i + 1][4] if kind == "section" and i + 1 < n else 0)  # pas de titre orphelin
        room = y - other_bottom - 18
        # si tout le reste tient ici mais pas les totaux : on garde 2 lignes pour la page finale
        hold = not fits_all and rem_h <= room and rem_lines <= 2 and not page_start
        if fits_all or (need <= room and not hold):
            if kind == "section":
                section = t
                y = draw_section(y, t)
            else:
                y = draw_row(y, l, t, d)
                running += l["amount"]
            page_start = False
            i += 1
            continue
        # plus de place : « à reporter » puis page suivante
        carry_line(y - 14, "À reporter", running)
        c.showPage()
        top_band(word, f"N° {invoice['number']} · suite")
        y = table_header(CONT_TOP)
        carry_line(y - 12, "Report", running)
        y -= 20
        page_start = True
        if mixed and section and kind != "section":
            y = draw_section(y, section, cont=True)
    if y < qr_bottom:  # les totaux ne tiennent pas avec le bulletin QR
        carry_line(y - 14, "À reporter", running)
        c.showPage()
        top_band(word, f"N° {invoice['number']} · suite")
        y = CONT_TOP
        carry_line(y, "Report", running)
        y -= 6

    # ---------------- totaux (sans cadre) ----------------
    y -= 16
    top_block = y
    c.setFont("Inter-Regular", 9.5)
    c.setFillColor(INK)
    c.drawString(tx_l, y, "Sous-total")
    c.drawRightString(X1, y, chf(invoice["subtotal"]))
    y -= 15
    if show_vat:
        c.drawString(tx_l, y, f"TVA {fpct(invoice['vat_rate'])} %")
        c.drawRightString(X1, y, chf(invoice["vat_amount"]))
        y -= 15
    if invoice.get("rounding"):
        c.setFillColor(MUTED)
        c.drawString(tx_l, y, "Arrondi")
        c.drawRightString(X1, y, chf(invoice["rounding"]))
        c.setFillColor(INK)
        y -= 15
    y += 4
    c.setStrokeColor(acc)
    c.setLineWidth(1.2)
    c.line(tx_l, y, X1, y)
    y -= 20
    c.setFillColor(INK)
    c.setFont("Inter-Medium", 10)
    c.drawString(tx_l, y, "Total CHF")
    c.setFillColor(acc)
    c.setFont("Inter-SemiBold", 16)
    c.drawRightString(X1, y - 1, chf(invoice["total"]))

    # ---------------- colonne gauche ----------------
    ly = top_block
    if hours_mode:
        c.setFont("Inter-Medium", 7)
        c.setFillColor(MUTED)
        c.drawString(X0, ly, "TOTAL HEURES")
        c.setFont("Inter-SemiBold", 11)
        c.setFillColor(INK)
        c.drawString(X0, ly - 14, f"{fnum(round(sum(l['quantity'] for l in h_lines), 2))} h")
        if annex:
            c.setFont("Inter-Regular", 8)
            c.setFillColor(MUTED)
            c.drawString(X0 + 22 * mm, ly - 14, "· détail des horaires en annexe")
        ly -= 32
    c.setFont("Inter-Regular", 8.5)
    c.setFillColor(MUTED)
    for t in terms_l:
        c.drawString(X0, ly, t)
        ly -= 11
    if notes_l:
        ly -= 4
        c.setFillColor(INK)
        for t in notes_l:
            c.drawString(X0, ly, t)
            ly -= 11

    footer = settings.get("invoice_footer", "")
    if footer:
        c.setFillColor(acc)
        c.setFont("Inter-Medium", 9)
        c.drawString(X0, zone_top + 7 * mm, footer)
    c.num_y = zone_top + 7 * mm

    if quote:
        # ---------------- bon pour accord ----------------
        if acceptance:
            by = 20 * mm
            c.setStrokeColor(HAIR)
            c.setLineWidth(0.6)
            c.line(X0, by + 30 * mm - 2 * mm, X1, by + 30 * mm - 2 * mm)
            c.setFont("Inter-Medium", 7)
            c.setFillColor(MUTED)
            c.drawString(X0, by + 22 * mm, "BON POUR ACCORD")
            c.setFont("Inter-Regular", 8.5)
            c.drawString(X0, by + 17.5 * mm, f"Le client accepte le présent devis N° {invoice['number']}.")
            half = (X1 - X0 - 12 * mm) / 2
            for k, lab in enumerate(("Lieu et date", "Nom et signature")):
                x = X0 + k * (half + 12 * mm)
                c.setStrokeColor(INK)
                c.setLineWidth(0.5)
                c.line(x, by + 3 * mm, x + half, by + 3 * mm)
                c.setFillColor(MUTED)
                c.setFont("Inter-Regular", 7.5)
                c.drawString(x, by - 1 * mm, lab)
        c.showPage()
    else:
        # ---------------- bulletin QR ----------------
        renderPDF.draw(qr, c, 0, 0)
        c.showPage()

    # ---------------- annexe : décompte des heures ----------------
    if annex:
        _draw_timesheet(c, top_band, settings, client, invoice, h_lines, acc)

    c.save()
    return path


def _draw_timesheet(c, top_band, settings, client, invoice, lines, acc):
    ts = sorted(lines, key=lambda l: (l.get("line_date") or "", l.get("start_time") or ""))
    cols = [("Date", X0, "l"), ("Prestation", X0 + 24 * mm, "l"), ("Détail", X0 + 62 * mm, "l"),
            ("Début", X1 - 42 * mm, "r"), ("Fin", X1 - 22 * mm, "r"), ("Heures", X1, "r")]
    detail_w = (X1 - 56 * mm) - (X0 + 62 * mm)

    def page_head(first):
        top_band("Heures estimées" if invoice.get("doc_type") == "quote" else "Décompte des heures", f"Annexe {'au devis' if invoice.get('doc_type') == 'quote' else 'à la facture'} N° {invoice['number']}" + ("" if first else " · suite"))
        y = PAGE_H - 34 * mm
        c.setFont("Inter-SemiBold", 10.5)
        c.setFillColor(INK)
        c.drawString(X0, y, client["name"])
        if invoice.get("event_name"):
            c.setFont("Inter-Regular", 9.5)
            c.setFillColor(MUTED)
            ev = " · ".join(v for v in (invoice["event_name"], fdate(invoice.get("event_date", "")),
                                        invoice.get("event_place", "")) if v)
            c.drawString(X0, y - 13, _ellipsis(ev, "Inter-Regular", 9.5, X1 - X0))
        y -= 34
        c.setFont("Inter-Medium", 7)
        c.setFillColor(MUTED)
        for lab, cx, al in cols:
            (c.drawRightString if al == "r" else c.drawString)(cx, y, lab.upper())
        c.setStrokeColor(INK)
        c.setLineWidth(0.6)
        c.line(X0, y - 5, X1, y - 5)
        return y - 7

    # synthèse par prestation
    summary = {}
    for l in ts:
        summary[l["rate_label"]] = summary.get(l["rate_label"], 0) + l["quantity"]
    summary_h = 30 + 14 * (len(summary) + 1)

    y = page_head(True)
    for idx, l in enumerate(ts):
        rem = len(ts) - idx
        need_break = y - TS_ROW_H < 22 * mm
        # garder quelques lignes avec le récapitulatif plutôt qu'une page de récap seule
        if not need_break and rem == 3 and y - rem * TS_ROW_H - summary_h - 22 < 22 * mm and idx > 0:
            need_break = True
        if need_break:
            c.showPage()
            y = page_head(False)
        base = y - 12.5
        c.setFillColor(INK)
        c.setFont("Inter-Regular", 9)
        c.drawString(X0, base, fdate(l.get("line_date", "")))
        c.setFont("Inter-Medium", 9)
        c.drawString(X0 + 24 * mm, base, _ellipsis(l["rate_label"], "Inter-Medium", 9, 36 * mm))
        c.setFont("Inter-Regular", 8.5)
        c.setFillColor(MUTED)
        c.drawString(X0 + 62 * mm, base, _ellipsis(l.get("description", ""), "Inter-Regular", 8.5, detail_w))
        c.setFillColor(INK)
        c.setFont("Inter-Regular", 9)
        c.drawRightString(X1 - 42 * mm, base, l.get("start_time") or "–")
        c.drawRightString(X1 - 22 * mm, base, l.get("end_time") or "–")
        c.setFont("Inter-Medium", 9)
        c.drawRightString(X1, base, fnum(l["quantity"]) + " h")
        c.setStrokeColor(HAIR)
        c.setLineWidth(0.5)
        c.line(X0, y - TS_ROW_H, X1, y - TS_ROW_H)
        y -= TS_ROW_H

    if y - summary_h < 22 * mm:
        c.showPage()
        top_band("Heures estimées" if invoice.get("doc_type") == "quote" else "Décompte des heures", f"Annexe à la facture N° {invoice['number']} · suite")
        y = PAGE_H - 34 * mm
    y -= 22
    sx = X1 - 72 * mm
    c.setFont("Inter-Medium", 7)
    c.setFillColor(MUTED)
    c.drawString(sx, y, "RÉCAPITULATIF")
    y -= 15
    c.setFont("Inter-Regular", 9.5)
    c.setFillColor(INK)
    for lab, h in summary.items():
        c.drawString(sx, y, _ellipsis(lab, "Inter-Regular", 9.5, 45 * mm))
        c.drawRightString(X1, y, f"{fnum(round(h, 2))} h")
        y -= 14
    y += 4
    c.setStrokeColor(acc)
    c.setLineWidth(1.2)
    c.line(sx, y, X1, y)
    y -= 16
    c.setFont("Inter-Medium", 10)
    c.drawString(sx, y, "Total")
    c.setFillColor(acc)
    c.setFont("Inter-SemiBold", 13)
    c.drawRightString(X1, y, f"{fnum(round(sum(summary.values()), 2))} h")
    c.showPage()
