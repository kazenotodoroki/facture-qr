"""FactureQR – facturation QR suisse pour indépendants.

Lancer :  python app.py   puis ouvrir http://127.0.0.1:5000
"""
import csv
import io
import json
import re
import unicodedata
import webbrowser
from datetime import date, datetime, timedelta
from pathlib import Path
from threading import Timer

from flask import (Flask, Response, abort, flash, jsonify, redirect, render_template,
                   request, send_file, url_for)

import backup
import db
import mailer
from pdfgen import chf, fdate, fnum, generate_invoice_pdf, hours_between
from references import format_reference, is_qr_iban, make_reference

VERSION = "1.6 · 22.10.2026"

app = Flask(__name__)
app.secret_key = "factureqr-local"
app.jinja_env.filters.update(chf=chf, fdate=fdate, fnum=fnum, fref=format_reference)

UPLOAD_DIR = db.DATA_DIR / "logo"
STATUSES = {"brouillon": "Brouillon", "envoyée": "Envoyée", "payée": "Payée", "annulée": "Annulée"}
QSTATUSES = {"brouillon": "Brouillon", "envoyé": "Envoyé", "accepté": "Accepté", "refusé": "Refusé"}


def is_quote(inv):
    return (inv.get("doc_type") if hasattr(inv, "get") else inv["doc_type"]) == "quote"


def doc_word(inv, cap=True):
    w = "devis" if is_quote(inv) else "facture"
    return w.capitalize() if cap else w


@app.context_processor
def inject():
    return {"VERSION": VERSION, "S": db.get_settings(), "STATUSES": STATUSES, "QSTATUSES": QSTATUSES, "backup_status": backup.last_status,
            "today": date.today().isoformat()}


def fold(s):
    """Minuscules sans accents, pour la recherche."""
    return unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower()


def slug(s):
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    return re.sub(r"[^A-Za-z0-9]+", "-", s).strip("-")[:40] or "client"


def after_change():
    backup.backup_async(db.get_settings())


# ------------------------------------------------------------------ tableau de bord
@app.route("/")
def dashboard():
    year = str(date.today().year)
    with db.connect() as conn:
        stats = conn.execute("""
            SELECT
              COALESCE(SUM(CASE WHEN status!='annulée' THEN total END),0) AS ca,
              COALESCE(SUM(CASE WHEN status='payée' THEN total END),0) AS paid,
              COALESCE(SUM(CASE WHEN status IN ('envoyée','brouillon') THEN total END),0) AS open,
              COUNT(*) AS n
            FROM invoices WHERE substr(issue_date,1,4)=? AND doc_type='invoice'""", (year,)).fetchone()
        quotes = conn.execute("""SELECT COUNT(*) AS n, COALESCE(SUM(total),0) AS total FROM invoices
            WHERE doc_type='quote' AND status IN ('brouillon','envoyé') AND valid_until >= ?""",
                              (date.today().isoformat(),)).fetchone()
        overdue = conn.execute("""SELECT i.*, c.name AS client_name FROM invoices i JOIN clients c ON c.id=i.client_id
            WHERE doc_type='invoice' AND status='envoyée' AND due_date < ? ORDER BY due_date""", (date.today().isoformat(),)).fetchall()
        recent = conn.execute("""SELECT i.*, c.name AS client_name FROM invoices i JOIN clients c ON c.id=i.client_id
            WHERE doc_type='invoice' ORDER BY i.id DESC LIMIT 8""").fetchall()
        nclients = conn.execute("SELECT COUNT(*) FROM clients WHERE archived=0").fetchone()[0]
    s = db.get_settings()
    setup_missing = [lab for key, lab in (("company_name", "nom de l'entreprise"), ("company_iban", "IBAN"),
                                          ("company_pcode", "adresse"), ("smtp_host", "serveur e-mail"),
                                          ("backup_dir", "dossier de sauvegarde")) if not s.get(key)]
    return render_template("dashboard.html", stats=stats, overdue=overdue, recent=recent, year=year, quotes=quotes,
                           nclients=nclients, setup_missing=setup_missing)


# ------------------------------------------------------------------ clients
@app.route("/clients")
def clients():
    show_archived = request.args.get("archives") == "1"
    q = request.args.get("q", "").strip()
    sql = """SELECT c.*, (SELECT COUNT(*) FROM invoices i WHERE i.client_id=c.id AND doc_type='invoice') AS ninv,
            (SELECT COALESCE(SUM(total),0) FROM invoices i WHERE i.client_id=c.id AND status!='annulée'
             AND doc_type='invoice') AS ca
            FROM clients c WHERE archived=?"""
    params = [1 if show_archived else 0]
    with db.connect() as conn:
        rows = conn.execute(sql + " ORDER BY name COLLATE NOCASE", params).fetchall()
        rates = conn.execute("SELECT * FROM rates ORDER BY id").fetchall()
    by_client = {}
    for r in rates:
        by_client.setdefault(r["client_id"], []).append(r)
    if q:  # recherche insensible aux accents et à la casse, sur tous les mots
        words = fold(q).split()
        def hay(c):
            return fold(" ".join([c["name"], c["contact"], c["city"], c["email"], c["notes"], c["pcode"]] +
                                 [r["label"] for r in by_client.get(c["id"], [])]))
        rows = [c for c in rows if all(w in hay(c) for w in words)]
    return render_template("clients.html", clients=rows, rates=by_client, show_archived=show_archived, q=q)


@app.route("/clients/nouveau", methods=["GET", "POST"])
@app.route("/clients/<int:cid>", methods=["GET", "POST"])
def client_edit(cid=None):
    fields = ["name", "contact", "street", "house_num", "pcode", "city", "country", "email", "cc_email", "notes"]
    if request.method == "POST":
        f = request.form
        vals = [f.get(k, "").strip() for k in fields]
        if not vals[0]:
            flash("Le nom du client est obligatoire.", "error")
            return redirect(request.url)
        vals[6] = (vals[6] or "CH").upper()[:2]
        with db.connect() as conn:
            if cid:
                conn.execute(f"UPDATE clients SET {', '.join(k + '=?' for k in fields)} WHERE id=?", vals + [cid])
            else:
                cur = conn.execute(f"INSERT INTO clients({', '.join(fields)}) VALUES ({', '.join('?' * len(fields))})", vals)
                cid = cur.lastrowid
            conn.execute("DELETE FROM rates WHERE client_id=?", (cid,))
            for label, rate in zip(f.getlist("rate_label"), f.getlist("rate_value")):
                label = label.strip()
                if label and rate.strip():
                    try:
                        conn.execute("INSERT INTO rates(client_id,label,rate) VALUES (?,?,?)",
                                     (cid, label, float(rate.replace("'", "").replace(",", "."))))
                    except ValueError:
                        pass
        after_change()
        flash("Client enregistré.", "ok")
        nxt = request.form.get("next")
        return redirect(nxt if nxt and nxt.startswith("/") else url_for("clients"))
    client, rates = None, []
    if cid:
        with db.connect() as conn:
            client = conn.execute("SELECT * FROM clients WHERE id=?", (cid,)).fetchone() or abort(404)
            rates = conn.execute("SELECT * FROM rates WHERE client_id=? ORDER BY id", (cid,)).fetchall()
    return render_template("client_form.html", client=client, rates=rates, nxt=request.args.get("next", ""))


@app.post("/clients/<int:cid>/archiver")
def client_archive(cid):
    with db.connect() as conn:
        conn.execute("UPDATE clients SET archived = 1 - archived WHERE id=?", (cid,))
    after_change()
    return redirect(url_for("clients"))


@app.get("/api/numero-libre")
def api_number_free():
    n = request.args.get("n", "").strip()
    iid = request.args.get("id", type=int)
    with db.connect() as conn:
        return jsonify(free=not n or not db.number_exists(conn, n, iid))


@app.get("/api/prochain-numero")
def api_next_number():
    s = db.get_settings()
    try:
        year = date.fromisoformat(request.args.get("date", "")).year
    except ValueError:
        year = date.today().year
    doc = "quote" if request.args.get("doc") == "quote" else "invoice"
    if s.get("invoice_numbering") == "random":
        return jsonify(hint="Auto (aléatoire)")
    with db.connect() as conn:
        return jsonify(hint=f"{db.new_invoice_number(conn, year, s, doc)} (auto)")


@app.get("/api/clients/<int:cid>")
def api_client(cid):
    with db.connect() as conn:
        c = conn.execute("SELECT * FROM clients WHERE id=?", (cid,)).fetchone() or abort(404)
        rates = conn.execute("SELECT id,label,rate FROM rates WHERE client_id=? ORDER BY id", (cid,)).fetchall()
        events = conn.execute("""SELECT DISTINCT event_name FROM invoices WHERE client_id=? AND event_name!=''
            ORDER BY id DESC LIMIT 15""", (cid,)).fetchall()
        flat = conn.execute("""SELECT rate_label FROM invoice_lines WHERE kind='flat' AND rate_label!=''
            GROUP BY rate_label ORDER BY MAX(id) DESC LIMIT 30""").fetchall()
    return jsonify(client=dict(c), rates=[dict(r) for r in rates], events=[e[0] for e in events],
                   flat_labels=[x[0] for x in flat])


# ------------------------------------------------------------------ factures
def _load_invoice(iid):
    with db.connect() as conn:
        inv = conn.execute("SELECT * FROM invoices WHERE id=?", (iid,)).fetchone() or abort(404)
        lines = conn.execute("SELECT * FROM invoice_lines WHERE invoice_id=? ORDER BY position", (iid,)).fetchall()
        client = conn.execute("SELECT * FROM clients WHERE id=?", (inv["client_id"],)).fetchone()
    snap = json.loads(inv["client_snapshot"] or "{}") or dict(client)
    return dict(inv), [dict(l) for l in lines], snap, dict(client)


def _num(v, default=0.0):
    try:
        return float(str(v).replace("'", "").replace(",", ".").strip())
    except (TypeError, ValueError):
        return default


def unique_reference(conn, iban, number, iid):
    """Référence de paiement unique (les numéros manuels peuvent partager les mêmes chiffres)."""
    ref = make_reference(iban, number, iid)
    clash = conn.execute("SELECT 1 FROM invoices WHERE reference=? AND id!=?", (ref, iid)).fetchone()
    if clash or len(re.sub(r"\D", "", number)) < 3:
        ref = make_reference(iban, re.sub(r"\D", "", number) + f"{iid:05d}", iid)
    return ref


def build_pdf(iid):
    inv, lines, snap, _ = _load_invoice(iid)
    s = db.get_settings()
    year = inv["issue_date"][:4]
    out_dir = db.PDF_DIR / year
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{doc_word(inv)}_{inv['number']}_{slug(snap['name'])}.pdf"
    if inv.get("pdf_path") and Path(inv["pdf_path"]) != path:
        Path(inv["pdf_path"]).unlink(missing_ok=True)
    generate_invoice_pdf(path, s, snap, inv, lines)
    with db.connect() as conn:
        conn.execute("UPDATE invoices SET pdf_path=? WHERE id=?", (str(path), iid))
    return path


@app.route("/factures")
def invoices():
    return _list_docs("invoice")


@app.route("/devis")
def quotes():
    return _list_docs("quote")


def _list_docs(doc_type):
    quote = doc_type == "quote"
    q = request.args.get("q", "").strip()
    cid = request.args.get("client", "")
    year = request.args.get("annee", "")
    status = request.args.get("statut", "")
    sql = """SELECT i.*, c.name AS client_name FROM invoices i JOIN clients c ON c.id=i.client_id WHERE i.doc_type=?"""
    params = [doc_type]
    if q:
        sql += " AND (i.number LIKE ? OR c.name LIKE ? OR i.event_name LIKE ? OR i.notes LIKE ?)"
        params += [f"%{q}%"] * 4
    if cid:
        sql += " AND i.client_id=?"
        params.append(cid)
    if year:
        sql += " AND substr(i.issue_date,1,4)=?"
        params.append(year)
    if status == "expiré":
        sql += " AND i.status IN ('brouillon','envoyé') AND i.valid_until < ?"
        params.append(date.today().isoformat())
    elif status:
        sql += " AND i.status=?"
        params.append(status)
    sql = sql.replace("SELECT i.*,", """SELECT i.*, (SELECT CASE WHEN COUNT(DISTINCT kind) > 1 THEN 'Heures + forfait'
               WHEN MAX(kind)='flat' THEN 'Forfait' ELSE 'Heures' END FROM invoice_lines WHERE invoice_id=i.id) AS kind_label,
               (SELECT number FROM invoices x WHERE x.id=i.converted_invoice_id) AS invoice_number,""", 1)
    sql += " ORDER BY i.issue_date DESC, i.id DESC"
    with db.connect() as conn:
        rows = conn.execute(sql, params).fetchall()
        all_clients = conn.execute("SELECT id,name FROM clients ORDER BY name COLLATE NOCASE").fetchall()
        years = [r[0] for r in conn.execute("SELECT DISTINCT substr(issue_date,1,4) FROM invoices WHERE doc_type=? "
                                            "ORDER BY 1 DESC", (doc_type,))]
    if request.args.get("format") == "csv":
        buf = io.StringIO()
        w = csv.writer(buf, delimiter=";")
        if quote:
            w.writerow(["Numéro", "Date", "Valable jusqu'au", "Client", "Événement", "Type", "Sous-total",
                        "TVA %", "TVA", "Total CHF", "Statut", "Envoyé le", "Facture"])
            for r in rows:
                w.writerow([r["number"], fdate(r["issue_date"]), fdate(r["valid_until"]), r["client_name"],
                            r["event_name"], r["kind_label"], f"{r['subtotal']:.2f}", r["vat_rate"],
                            f"{r['vat_amount']:.2f}", f"{r['total']:.2f}", QSTATUSES.get(r["status"]),
                            r["sent_at"][:10], r["invoice_number"] or ""])
        else:
            w.writerow(["Numéro", "Date", "Échéance", "Client", "Événement", "Type", "Sous-total",
                        "TVA %", "TVA", "Arrondi", "Total CHF", "Statut", "Envoyée le", "Payée le", "Référence"])
            for r in rows:
                w.writerow([r["number"], fdate(r["issue_date"]), fdate(r["due_date"]), r["client_name"], r["event_name"],
                            r["kind_label"], f"{r['subtotal']:.2f}", r["vat_rate"],
                            f"{r['vat_amount']:.2f}", f"{r['rounding']:.2f}", f"{r['total']:.2f}", STATUSES.get(r["status"]),
                            r["sent_at"][:10], fdate(r["paid_at"][:10]), r["reference"]])
        name = f"{'devis' if quote else 'factures'}_{year or 'tous'}.csv"
        return Response("﻿" + buf.getvalue(), mimetype="text/csv",
                        headers={"Content-Disposition": f"attachment; filename={name}"})
    total = sum(r["total"] for r in rows if r["status"] not in ("annulée", "refusé"))
    return render_template("invoices.html", invoices=rows, clients=all_clients, years=years, total=total,
                           f=dict(q=q, client=cid, annee=year, statut=status), Q=quote,
                           ST=QSTATUSES if quote else STATUSES, list_ep="quotes" if quote else "invoices")


@app.route("/devis/nouveau", methods=["GET", "POST"])
def quote_form():
    return invoice_form(doc_type="quote")


@app.route("/factures/nouvelle", methods=["GET", "POST"])
@app.route("/factures/<int:iid>/modifier", methods=["GET", "POST"])
def invoice_form(iid=None, doc_type="invoice"):
    s = db.get_settings()
    if iid:
        with db.connect() as conn:
            row = conn.execute("SELECT doc_type FROM invoices WHERE id=?", (iid,)).fetchone() or abort(404)
        doc_type = row["doc_type"] or "invoice"
    if request.method == "POST":
        return _save_invoice(iid, s, doc_type)
    with db.connect() as conn:
        all_clients = conn.execute("""SELECT id,name,city,contact,email FROM clients WHERE archived=0
            ORDER BY name COLLATE NOCASE""").fetchall()
    inv, lines = None, []
    if iid:
        inv, lines, _, _ = _load_invoice(iid)
        if inv["status"] not in ("brouillon",):
            flash(f"{'Ce devis' if doc_type == 'quote' else 'Cette facture'} a déjà été envoyé{'' if doc_type == 'quote' else 'e'} : "
                  "modifiez avec prudence.", "warn")
    preset_client = request.args.get("client", type=int)
    return render_template("invoice_form.html", clients=[dict(c) for c in all_clients], inv=inv, lines=lines,
                           preset_client=preset_client, payment_days=int(s.get("payment_days") or 30),
                           Q=doc_type == "quote", validity_days=int(s.get("quote_validity_days") or 30))


def _save_invoice(iid, s, doc_type="invoice"):
    quote = doc_type == "quote"
    word = "Devis" if quote else "Facture"
    f = request.form
    client_id = f.get("client_id", type=int)
    if not client_id:
        flash("Choisissez un client.", "error")
        return redirect(request.url)
    mode = f.get("mode") if f.get("mode") in ("hours", "flat", "mixed") else "hours"
    checked = lambda k: f.get(k) not in (None, "", "0")
    wanted = re.sub(r"\s+", " ", f.get("number", "")).strip()[:30]
    if wanted and not re.fullmatch(r"[A-Za-z0-9._/\- ]+", wanted):
        flash(f"Numéro : lettres, chiffres, espaces et - . / _ uniquement.", "error")
        return redirect(request.url)
    has_event = f.get("has_event") == "1"
    issue = f.get("issue_date") or date.today().isoformat()
    due = (date.fromisoformat(issue) + timedelta(days=int(s.get("payment_days") or 30))).isoformat()
    valid_until = ""
    if quote:
        valid_until = f.get("valid_until") or (date.fromisoformat(issue) +
                                                timedelta(days=int(s.get("quote_validity_days") or 30))).isoformat()
        if valid_until < issue:
            flash("La date de validité doit être postérieure à la date du devis.", "error")
            return redirect(request.url)
        due = valid_until

    lines = []
    if mode in ("hours", "mixed"):
        for d, st, en, lab, rate, qty, desc in zip(
                f.getlist("h_date"), f.getlist("h_start"), f.getlist("h_end"), f.getlist("h_label"),
                f.getlist("h_rate"), f.getlist("h_qty"), f.getlist("h_desc")):
            q, r = _num(qty), _num(rate)
            if not q and st and en:
                q = hours_between(st, en) or 0
            if q and lab.strip():
                lines.append(dict(kind="hours", line_date=d, start_time=st, end_time=en, rate_label=lab.strip(),
                                  description=desc.strip(), quantity=q, unit_price=r, amount=round(q * r, 2)))
    if mode in ("flat", "mixed"):
        for lab, desc, qty, price in zip(f.getlist("f_label"), f.getlist("f_desc"), f.getlist("f_qty"),
                                         f.getlist("f_price")):
            q, p = _num(qty, 1.0) or 1.0, _num(price)
            if lab.strip() and p:
                lines.append(dict(kind="flat", line_date="", start_time="", end_time="", rate_label=lab.strip(),
                                  description=desc.strip(), quantity=q, unit_price=p, amount=round(q * p, 2)))
    if not lines:
        flash("Ajoutez au moins une ligne (heures ou forfait).", "error")
        return redirect(request.url)

    has_hours = any(l["kind"] == "hours" for l in lines)
    subtotal = round(sum(l["amount"] for l in lines), 2)
    show_vat = 1 if s.get("show_vat") == "1" else 0
    vat_rate = _num(s.get("vat_rate")) if show_vat else 0.0
    vat = round(subtotal * vat_rate / 100, 2)
    raw = round(subtotal + vat, 2)
    total = round(round(raw * 20) / 20, 2) if s.get("round_5ct") == "1" else raw
    rounding = round(total - raw, 2)

    with db.connect() as conn:
        client = dict(conn.execute("SELECT * FROM clients WHERE id=?", (client_id,)).fetchone())
        data = dict(client_id=client_id, client_snapshot=json.dumps(client, ensure_ascii=False),
                    issue_date=issue, due_date=due,
                    event_name=f.get("event_name", "").strip() if has_event else "",
                    event_date=f.get("event_date", "") if has_event else "",
                    event_place=f.get("event_place", "").strip() if has_event else "",
                    mode="hours" if has_hours else "flat", subtotal=subtotal, vat_rate=vat_rate, vat_amount=vat, rounding=rounding,
                    total=total, notes=f.get("notes", "").strip(), show_vat=show_vat,
                    show_terms=1 if checked("show_terms") else 0,
                    show_due=1 if checked("show_due") else 0,
                    show_timesheet=1 if (has_hours and checked("show_timesheet")) else 0,
                    summary_mode="grouped" if f.get("summary_mode") == "grouped" else "lines",
                    doc_type=doc_type, valid_until=valid_until)
        if wanted and db.number_exists(conn, wanted, iid):
            flash(f"Le numéro {wanted} est déjà utilisé.", "error")
            return redirect(request.url)
        if iid:
            old = conn.execute("SELECT number FROM invoices WHERE id=?", (iid,)).fetchone()["number"]
            if wanted and wanted != old:
                data["number"] = wanted
                if not quote:
                    data["reference"] = unique_reference(conn, s["company_iban"], wanted, iid)
            conn.execute(f"UPDATE invoices SET {', '.join(k + '=?' for k in data)} WHERE id=?",
                         list(data.values()) + [iid])
            conn.execute("DELETE FROM invoice_lines WHERE invoice_id=?", (iid,))
        else:
            number = wanted or db.new_invoice_number(conn, int(issue[:4]), s, doc_type)
            data["number"] = number
            cur = conn.execute(f"INSERT INTO invoices({', '.join(data)}) VALUES ({', '.join('?' * len(data))})",
                               list(data.values()))
            iid = cur.lastrowid
            if not quote:
                conn.execute("UPDATE invoices SET reference=? WHERE id=?",
                             (unique_reference(conn, s["company_iban"], number, iid), iid))
        for i, l in enumerate(lines):
            conn.execute("""INSERT INTO invoice_lines(invoice_id,position,kind,line_date,start_time,end_time,
                description,rate_label,quantity,unit_price,amount) VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                         (iid, i, l["kind"], l["line_date"], l["start_time"], l["end_time"], l["description"],
                          l["rate_label"], l["quantity"], l["unit_price"], l["amount"]))
    try:
        build_pdf(iid)
    except Exception as exc:
        flash(f"{word} enregistré{'' if quote else 'e'} mais le PDF n'a pas pu être créé : {exc}", "error")
        return redirect(url_for("invoice_view", iid=iid))
    after_change()
    flash(f"{word} enregistré{'' if quote else 'e'} et PDF généré.", "ok")
    if f.get("then") == "send":
        return redirect(url_for("invoice_view", iid=iid, envoyer=1))
    return redirect(url_for("invoice_view", iid=iid))


@app.route("/factures/<int:iid>")
def invoice_view(iid):
    inv, lines, snap, client = _load_invoice(iid)
    s = db.get_settings()
    links = {}
    with db.connect() as conn:
        if inv.get("converted_invoice_id"):
            r = conn.execute("SELECT id, number FROM invoices WHERE id=?", (inv["converted_invoice_id"],)).fetchone()
            links["invoice"] = dict(r) if r else None
        if inv.get("quote_id"):
            r = conn.execute("SELECT id, number FROM invoices WHERE id=?", (inv["quote_id"],)).fetchone()
            links["quote"] = dict(r) if r else None
    Q = is_quote(inv)
    return render_template("invoice_view.html", inv=inv, lines=lines, client=snap, live_client=client,
                           Q=Q, ST=QSTATUSES if Q else STATUSES, links=links,
                           subject=mailer.subject(s, snap, inv), message=mailer.message_text(s, snap, inv),
                           open_send=request.args.get("envoyer") == "1")


@app.route("/factures/<int:iid>/pdf")
def invoice_pdf(iid):
    inv, *_ = _load_invoice(iid)
    path = Path(inv["pdf_path"]) if inv["pdf_path"] else None
    if not path or not path.exists():
        path = build_pdf(iid)
    return send_file(path, mimetype="application/pdf", as_attachment=request.args.get("dl") == "1",
                     download_name=path.name)


@app.post("/factures/<int:iid>/regenerer")
def invoice_regen(iid):
    build_pdf(iid)
    after_change()
    flash("PDF régénéré avec les paramètres actuels.", "ok")
    return redirect(url_for("invoice_view", iid=iid))


@app.route("/factures/<int:iid>/apercu-mail")
def invoice_mail_preview(iid):
    inv, _, snap, _ = _load_invoice(iid)
    s = db.get_settings()
    msg = request.args.get("message")
    att = Path(inv["pdf_path"]).name if inv.get("pdf_path") else None
    return mailer.render_html(s, snap, inv, logo_src=url_for("logo") if s.get("company_logo") else None,
                              message=msg, attachment_name=att)


@app.post("/factures/<int:iid>/envoyer")
def invoice_send(iid):
    inv, _, snap, _ = _load_invoice(iid)
    s = db.get_settings()
    split = lambda v: [a.strip() for a in re.split(r"[,;\s]+", v or "") if a.strip()]
    to, cc = split(request.form.get("to")), split(request.form.get("cc"))
    if not to:
        flash("Indiquez au moins une adresse e-mail.", "error")
        return redirect(url_for("invoice_view", iid=iid, envoyer=1))
    key = "mail_quote_subject" if is_quote(inv) else "mail_subject"
    s[key] = request.form.get("subject") or s[key]
    path = Path(inv["pdf_path"]) if inv["pdf_path"] and Path(inv["pdf_path"]).exists() else build_pdf(iid)
    try:
        mailer.send_invoice(s, snap, inv, path, to, cc, message=request.form.get("message"))
    except Exception as exc:
        flash(f"Envoi impossible : {exc}", "error")
        return redirect(url_for("invoice_view", iid=iid, envoyer=1))
    with db.connect() as conn:
        conn.execute("""UPDATE invoices SET sent_at=?, sent_to=?,
                        status=CASE WHEN status='brouillon' THEN ? ELSE status END WHERE id=?""",
                     (datetime.now().strftime("%Y-%m-%d %H:%M"), ", ".join(to + cc),
                      "envoyé" if is_quote(inv) else "envoyée", iid))
    after_change()
    flash(f"{doc_word(inv)} envoyé{'' if is_quote(inv) else 'e'} à {', '.join(to + cc)}.", "ok")
    return redirect(url_for("invoice_view", iid=iid))


@app.post("/factures/<int:iid>/statut")
def invoice_status(iid):
    st = request.form.get("status")
    with db.connect() as conn:
        dt = conn.execute("SELECT doc_type FROM invoices WHERE id=?", (iid,)).fetchone()["doc_type"]
    if st in (QSTATUSES if dt == "quote" else STATUSES):
        with db.connect() as conn:
            paid = request.form.get("paid_at") or date.today().isoformat() if st == "payée" else ""
            conn.execute("UPDATE invoices SET status=?, paid_at=? WHERE id=?", (st, paid, iid))
        after_change()
    return redirect(request.referrer or url_for("invoice_view", iid=iid))


def _delete_invoices(ids):
    """Supprime des factures (lignes + PDF). Retourne les numéros supprimés."""
    done = []
    with db.connect() as conn:
        for iid in ids:
            row = conn.execute("SELECT number, pdf_path FROM invoices WHERE id=?", (iid,)).fetchone()
            if not row:
                continue
            conn.execute("DELETE FROM invoice_lines WHERE invoice_id=?", (iid,))
            conn.execute("DELETE FROM invoices WHERE id=?", (iid,))
            if row["pdf_path"]:
                Path(row["pdf_path"]).unlink(missing_ok=True)
            done.append(row["number"])
    return done


@app.post("/factures/<int:iid>/supprimer")
def invoice_delete(iid):
    inv, *_ = _load_invoice(iid)
    # une facture déjà envoyée/payée demande de retaper son numéro
    if inv["status"] != "brouillon" and request.form.get("confirm", "").strip() != inv["number"]:
        flash("Suppression annulée : le numéro saisi ne correspond pas.", "error")
        return redirect(url_for("invoice_view", iid=iid))
    _delete_invoices([iid])
    with db.connect() as conn:  # le lien depuis/vers un devis est retiré
        conn.execute("UPDATE invoices SET converted_invoice_id=NULL WHERE converted_invoice_id=?", (iid,))
        conn.execute("UPDATE invoices SET quote_id=NULL WHERE quote_id=?", (iid,))
    after_change()
    flash(f"{doc_word(inv)} {inv['number']} supprimé{'' if is_quote(inv) else 'e'}.", "ok")
    return redirect(url_for("quotes" if is_quote(inv) else "invoices"))


@app.post("/factures/supprimer")
def invoices_delete_bulk():
    ids = [int(x) for x in request.form.getlist("ids") if x.isdigit()]
    if not ids:
        flash("Aucune facture sélectionnée.", "error")
    else:
        done = _delete_invoices(ids)
        with db.connect() as conn:
            for iid in ids:
                conn.execute("UPDATE invoices SET converted_invoice_id=NULL WHERE converted_invoice_id=?", (iid,))
                conn.execute("UPDATE invoices SET quote_id=NULL WHERE quote_id=?", (iid,))
        after_change()
        flash(f"{len(done)} document{'s' if len(done) > 1 else ''} supprimé{'s' if len(done) > 1 else ''} : "
              f"{', '.join(done)}.", "ok")
    return redirect(request.form.get("back") or url_for("invoices"))


def _copy_document(src, lines, s, doc_type, quote_id=None):
    """Crée un nouveau document (facture ou devis) à partir d'un autre, daté d'aujourd'hui."""
    today = date.today()
    with db.connect() as conn:
        number = db.new_invoice_number(conn, today.year, s, doc_type)
        cols = ["client_id", "client_snapshot", "event_name", "event_date", "event_place", "mode", "subtotal",
                "vat_rate", "vat_amount", "rounding", "total", "notes",
                "show_vat", "show_terms", "show_timesheet", "summary_mode", "show_due"]
        vals = [src[k] for k in cols]
        if doc_type == "quote":
            valid = (today + timedelta(days=int(s.get("quote_validity_days") or 30))).isoformat()
            due = valid
        else:
            valid = ""
            due = (today + timedelta(days=int(s.get("payment_days") or 30))).isoformat()
            if src.get("doc_type") == "quote":   # devis → facture : réglages de facture par défaut
                vals[cols.index("show_terms")] = 1 if s.get("show_payment_terms") == "1" else 0
                vals[cols.index("show_due")] = 1 if s.get("show_due_date") == "1" else 0
        cur = conn.execute(f"""INSERT INTO invoices(number, issue_date, due_date, doc_type, valid_until, quote_id,
            {', '.join(cols)}) VALUES (?,?,?,?,?,?,{', '.join('?' * len(cols))})""",
                           [number, today.isoformat(), due, doc_type, valid, quote_id] + vals)
        nid = cur.lastrowid
        if doc_type == "invoice":
            conn.execute("UPDATE invoices SET reference=? WHERE id=?",
                         (unique_reference(conn, s["company_iban"], number, nid), nid))
        for l in lines:
            conn.execute("""INSERT INTO invoice_lines(invoice_id,position,kind,line_date,start_time,end_time,
                description,rate_label,quantity,unit_price,amount) VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                         (nid, l["position"], l["kind"], l["line_date"], l["start_time"], l["end_time"],
                          l["description"], l["rate_label"], l["quantity"], l["unit_price"], l["amount"]))
    return nid, number


@app.post("/factures/<int:iid>/dupliquer")
def invoice_duplicate(iid):
    inv, lines, *_ = _load_invoice(iid)
    nid, number = _copy_document(inv, lines, db.get_settings(), inv["doc_type"] or "invoice")
    flash(f"Copie créée ({number}). Ajustez-la puis enregistrez.", "ok")
    return redirect(url_for("invoice_form", iid=nid))


@app.post("/devis/<int:iid>/facturer")
def quote_to_invoice(iid):
    inv, lines, *_ = _load_invoice(iid)
    if not is_quote(inv):
        abort(400)
    if inv.get("converted_invoice_id"):
        with db.connect() as conn:
            if conn.execute("SELECT 1 FROM invoices WHERE id=?", (inv["converted_invoice_id"],)).fetchone():
                flash("Ce devis a déjà été transformé en facture.", "warn")
                return redirect(url_for("invoice_view", iid=inv["converted_invoice_id"]))
    nid, number = _copy_document(inv, lines, db.get_settings(), "invoice", quote_id=iid)
    with db.connect() as conn:
        conn.execute("UPDATE invoices SET status='accepté', converted_invoice_id=? WHERE id=?", (nid, iid))
    build_pdf(nid)
    after_change()
    flash(f"Facture {number} créée depuis le devis {inv['number']}. Ajustez les heures réelles si besoin, "
          "puis enregistrez.", "ok")
    return redirect(url_for("invoice_form", iid=nid))


# ------------------------------------------------------------------ paramètres
def _save_settings_from_form():
    f = request.form
    vals = {k: f.get(k, "").strip() for k in db.DEFAULT_SETTINGS if k in f}
    if f.get("_section") == "company":
        for cb in ("round_5ct", "show_vat", "show_payment_terms", "default_timesheet", "show_due_date",
                   "random_with_year", "quote_show_acceptance"):
            vals[cb] = "1" if f.get(cb) else "0"
    if f.get("_section") == "mail":
        vals["smtp_bcc_self"] = "1" if f.get("smtp_bcc_self") else "0"
    if f.get("_section") == "design":
        vals["mail_show_summary"] = "1" if f.get("mail_show_summary") else "0"
    if "smtp_password" in vals and not vals["smtp_password"]:
        vals.pop("smtp_password")  # vide = on garde l'ancien
    if "company_iban" in vals:
        vals["company_iban"] = re.sub(r"\s+", "", vals["company_iban"]).upper()
    logo = request.files.get("logo")
    if logo and logo.filename:
        UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
        ext = Path(logo.filename).suffix.lower()
        if ext in (".png", ".jpg", ".jpeg"):
            for old in UPLOAD_DIR.glob("logo.*"):
                old.unlink()
            dest = UPLOAD_DIR / f"logo{ext}"
            logo.save(dest)
            vals["company_logo"] = str(dest)
        else:
            flash("Logo : formats PNG ou JPG uniquement.", "error")
    if f.get("remove_logo"):
        vals["company_logo"] = ""
    db.save_settings(vals)


@app.route("/parametres", methods=["GET", "POST"])
def settings():
    if request.method == "POST":
        _save_settings_from_form()
        after_change()
        flash("Paramètres enregistrés.", "ok")
        return redirect(url_for("settings", tab=request.form.get("_section", "company")))
    s = db.get_settings()
    return render_template("settings.html", tab=request.args.get("tab", "company"),
                           qr_iban=is_qr_iban(s.get("company_iban", "")))


@app.route("/parametres/apercu-mail")
def settings_mail_preview():
    s = db.get_settings()
    for k in ("mail_primary", "mail_accent", "mail_background", "mail_card", "mail_text"):
        if request.args.get(k):
            s[k] = request.args[k]
    for k in ("mail_message", "mail_signature_extra", "mail_show_summary"):
        if request.args.get(k) is not None:
            s[k] = request.args[k]
    for k in ("mail_quote_message",):
        if request.args.get(k) is not None:
            s[k] = request.args[k]
    client = {"name": "Théâtre Exemple"}
    quote = request.args.get("doc") == "quote"
    num = ("D-" if quote else "") + f"{date.today().year}-042"
    inv = {"number": num, "total": 2894.40, "event_name": "Le Songe d'une nuit d'été",
           "due_date": (date.today() + timedelta(days=30)).isoformat(), "show_due": s.get("show_due_date") == "1",
           "doc_type": "quote" if quote else "invoice",
           "valid_until": (date.today() + timedelta(days=int(s.get("quote_validity_days") or 30))).isoformat()}
    if not s.get("company_name"):
        s["company_name"] = "Votre entreprise"
    return mailer.render_html(s, client, inv, logo_src=url_for("logo") if s.get("company_logo") else None,
                              attachment_name=f"Facture_{num}_Theatre-Exemple.pdf")


@app.post("/parametres/test-mail")
def settings_test_mail():
    _save_settings_from_form()
    s = db.get_settings()
    import smtplib, ssl
    try:
        ctx = ssl.create_default_context()
        port = int(s.get("smtp_port") or 587)
        if s.get("smtp_security") == "ssl":
            srv = smtplib.SMTP_SSL(s["smtp_host"], port, context=ctx, timeout=20)
        else:
            srv = smtplib.SMTP(s["smtp_host"], port, timeout=20)
            if s.get("smtp_security") == "starttls":
                srv.starttls(context=ctx)
        if s.get("smtp_user"):
            srv.login(s["smtp_user"], s.get("smtp_password", ""))
        srv.quit()
        flash("Connexion au serveur e-mail réussie ✓", "ok")
    except Exception as exc:
        flash(f"Connexion impossible : {exc}", "error")
    return redirect(url_for("settings", tab="mail"))


@app.post("/parametres/sauvegarder-maintenant")
def backup_now():
    _save_settings_from_form()
    st = backup.run_backup(db.get_settings())
    flash(st["message"], "ok" if st["ok"] else "error")
    return redirect(url_for("settings", tab="backup"))


@app.route("/logo")
def logo():
    s = db.get_settings()
    p = s.get("company_logo")
    if not p or not Path(p).exists():
        abort(404)
    return send_file(p)


def main():
    db.init_db()
    backup.backup_async(db.get_settings())
    port = 5000
    print(f"FactureQR version {VERSION} – dossier : {Path(__file__).resolve().parent}")
    Timer(1.2, lambda: webbrowser.open(f"http://127.0.0.1:{port}")).start()
    app.run(host="127.0.0.1", port=port, debug=False)


if __name__ == "__main__":
    main()
