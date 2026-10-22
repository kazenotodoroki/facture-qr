"""E-mail HTML (compatible clients mail) + envoi SMTP avec le PDF en pièce jointe."""
import html
import mimetypes
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formataddr, make_msgid
from pathlib import Path

from pdfgen import chf, fdate


def _q(invoice):
    return invoice.get("doc_type") == "quote"


def _fill(template: str, settings, client, invoice) -> str:
    ev = f" pour « {invoice['event_name']} »" if invoice.get("event_name") else ""
    return (template or "").format(
        numero=invoice["number"], entreprise=settings["company_name"], client=client["name"],
        montant=chf(invoice["total"]), echeance=fdate(invoice.get("due_date", "")), evenement=ev,
        validite=fdate(invoice.get("valid_until", "")),
    )


def subject(settings, client, invoice):
    try:
        return _fill(settings.get("mail_quote_subject" if _q(invoice) else "mail_subject"), settings, client, invoice)
    except (KeyError, IndexError, ValueError):
        return f"{'Devis' if _q(invoice) else 'Facture'} {invoice['number']} – {settings['company_name']}"


def message_text(settings, client, invoice):
    try:
        return _fill(settings.get("mail_quote_message" if _q(invoice) else "mail_message"), settings, client, invoice)
    except (KeyError, IndexError, ValueError):
        if _q(invoice):
            return f"Bonjour,\n\nVous trouverez ci-joint le devis {invoice['number']}.\n\nMeilleures salutations"
        return f"Bonjour,\n\nVous trouverez ci-joint la facture {invoice['number']}.\n\nMeilleures salutations"


def render_html(settings, client, invoice, logo_src=None, message=None, attachment_name=None):
    """Page centrée aux angles droits, à base de tableaux (Outlook, Gmail, Apple Mail…)."""
    P = settings.get("mail_primary") or "#1F3A5F"
    A = settings.get("mail_accent") or "#E9B44C"
    BG = settings.get("mail_background") or "#F4F4F4"
    CARD = settings.get("mail_card") or "#FFFFFF"
    T = settings.get("mail_text") or "#1D1D1F"
    e = html.escape
    font = "Helvetica Neue,Helvetica,Arial,sans-serif"
    msg = message if message is not None else message_text(settings, client, invoice)
    paragraphs = "".join(
        f'<p style="margin:0 0 18px 0;">{e(p.strip(chr(10))).replace(chr(10), "<br>")}</p>'
        for p in msg.split("\n\n") if p.strip())
    quote = _q(invoice)
    word = "Devis" if quote else "Facture"
    show_due = bool(invoice.get("show_due", 1)) and invoice.get("due_date") and not quote
    label = "font-size:10px;letter-spacing:1.4px;text-transform:uppercase;color:#8A8A8E;padding-bottom:6px;"

    # --- récapitulatif en cases carrées ---
    summary = ""
    if settings.get("mail_show_summary", "1") == "1":
        cells = [("Montant", f"CHF&nbsp;{chf(invoice['total'])}", True), ("N°", e(invoice["number"]), False)]
        if quote and invoice.get("valid_until"):
            cells.append(("Valable jusqu'au", fdate(invoice["valid_until"]), False))
        elif show_due:
            cells.append(("Échéance", fdate(invoice["due_date"]), False))
        w = int(100 / len(cells))
        tds = []
        for k, (lab, val, big) in enumerate(cells):
            border_l = "border-left:1px solid #E4E4E7;" if k else ""
            tds.append(f"""<td width="{w}%" valign="top" style="{border_l}padding:16px 18px;">
              <div style="{label}">{lab}</div>
              <div style="font-size:{'19px' if big else '15px'};font-weight:bold;color:{P if big else T};">{val}</div></td>""")
        event = ""
        if invoice.get("event_name"):
            event = f"""<tr><td colspan="{len(cells)}" style="border-top:1px solid #E4E4E7;padding:14px 18px;">
              <div style="{label}">Événement</div>
              <div style="font-size:15px;font-weight:bold;color:{T};">{e(invoice['event_name'])}</div></td></tr>"""
        summary = f"""
    <tr><td style="padding:6px 48px 0 48px;">
      <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="border:1px solid #E4E4E7;border-top:3px solid {A};">
        <tr>{''.join(tds)}</tr>{event}
      </table>
    </td></tr>"""

    # --- signature ---
    sig = [f'<strong>{e(settings["company_name"])}</strong>']
    street = f"{settings.get('company_street', '')} {settings.get('company_house_num', '')}".strip()
    city = f"{settings.get('company_pcode', '')} {settings.get('company_city', '')}".strip()
    if street:
        sig.append(e(street))
    if city:
        country = settings.get("company_country") or "CH"
        sig.append(e(f"{country}-{city}" if country else city))
    link = f"color:{P};text-decoration:underline;"
    if settings.get("company_phone"):
        tel = "".join(ch for ch in settings["company_phone"] if ch.isdigit() or ch == "+")
        sig.append(f'<a href="tel:{tel}" style="{link}">{e(settings["company_phone"])}</a>')
    if settings.get("company_website"):
        url = settings["company_website"]
        href = url if url.startswith("http") else "https://" + url
        sig.append(f'<a href="{e(href)}" style="{link}">{e(url)}</a>')
    if settings.get("company_email"):
        sig.append(f'<a href="mailto:{e(settings["company_email"])}" style="{link}">{e(settings["company_email"])}</a>')
    logo = (f'<tr><td style="padding:18px 48px 0 48px;"><img src="{logo_src}" alt="{e(settings["company_name"])}" '
            f'height="44" style="display:block;height:44px;max-width:220px;border:0;"></td></tr>') if logo_src else ""
    extra = settings.get("mail_signature_extra", "").strip()
    extra_html = (f'<tr><td style="padding:22px 48px 0 48px;font-size:11px;line-height:1.6;color:#8A8A8E;">'
                  f'{e(extra).replace(chr(10), "<br>")}</td></tr>') if extra else ""

    preheader = f"{word} {invoice['number']} · CHF {chf(invoice['total'])}" + (
        f" · échéance {fdate(invoice['due_date'])}" if show_due else "") + (
        f" · valable jusqu'au {fdate(invoice['valid_until'])}" if quote and invoice.get("valid_until") else "")
    return f"""<!doctype html>
<html lang="fr"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="color-scheme" content="light"><title>{e(subject(settings, client, invoice))}</title></head>
<body style="margin:0;padding:0;background:{BG};">
<div style="display:none;max-height:0;overflow:hidden;opacity:0;">{e(preheader)}</div>
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:{BG};">
<tr><td align="center" style="padding:48px 12px;">
  <table role="presentation" width="600" cellpadding="0" cellspacing="0"
         style="width:100%;max-width:600px;background:{CARD};border:1px solid #E1E1E4;font-family:{font};color:{T};">
    <tr><td style="background:{P};padding:34px 48px 36px 48px;">
      <div style="font-size:11px;letter-spacing:1.6px;text-transform:uppercase;color:#FFFFFF;opacity:.75;padding-bottom:8px;">{e(settings['company_name'])}</div>
      <div style="font-size:30px;line-height:1.15;color:#FFFFFF;">{word} n°{e(invoice['number'])}</div>
    </td></tr>
    <tr><td style="padding:46px 48px 14px 48px;font-size:15px;line-height:1.6;color:{T};">
      {paragraphs}
    </td></tr>{summary}
    <tr><td style="padding:40px 48px 0 48px;font-size:14px;line-height:1.55;color:{T};">
      {'<br>'.join(sig)}
    </td></tr>{logo}{extra_html}
    <tr><td style="height:44px;line-height:44px;font-size:0;">&nbsp;</td></tr>
  </table>
</td></tr></table>
</body></html>"""


def send_invoice(settings, client, invoice, pdf_path, to_addrs, cc_addrs=(), message=None):
    if not settings.get("smtp_host"):
        raise RuntimeError("Serveur SMTP non configuré (Paramètres → E-mail).")
    sender_email = settings.get("company_email") or settings.get("smtp_user")
    msg = EmailMessage()
    msg["Subject"] = subject(settings, client, invoice)
    msg["From"] = formataddr((settings.get("smtp_from_name") or settings["company_name"], sender_email))
    msg["To"] = ", ".join(to_addrs)
    if cc_addrs:
        msg["Cc"] = ", ".join(cc_addrs)
    msg["Reply-To"] = sender_email
    text = message if message is not None else message_text(settings, client, invoice)
    if _q(invoice):
        due = f" – valable jusqu'au {fdate(invoice['valid_until'])}" if invoice.get("valid_until") else ""
    else:
        due = f" – échéance {fdate(invoice['due_date'])}" if invoice.get("show_due", 1) else ""
    msg.set_content(text + f"\n\n{'Devis' if _q(invoice) else 'Facture'} {invoice['number']} – "
                           f"CHF {chf(invoice['total'])}{due}\n")

    logo = settings.get("company_logo")
    logo_cid = None
    if logo and Path(logo).exists():
        logo_cid = make_msgid(domain="facture.local")
    msg.add_alternative(render_html(settings, client, invoice,
                                    logo_src=f"cid:{logo_cid[1:-1]}" if logo_cid else None,
                                    message=text, attachment_name=Path(pdf_path).name), subtype="html")
    if logo_cid:
        ctype = mimetypes.guess_type(logo)[0] or "image/png"
        maintype, subtype_ = ctype.split("/")
        msg.get_payload()[1].add_related(Path(logo).read_bytes(), maintype, subtype_, cid=logo_cid)

    msg.add_attachment(Path(pdf_path).read_bytes(), maintype="application", subtype="pdf",
                       filename=Path(pdf_path).name)

    recipients = list(to_addrs) + list(cc_addrs)
    if settings.get("smtp_bcc_self") == "1" and sender_email:
        recipients.append(sender_email)

    host, port = settings["smtp_host"], int(settings.get("smtp_port") or 587)
    ctx = ssl.create_default_context()
    if settings.get("smtp_security") == "ssl":
        server = smtplib.SMTP_SSL(host, port, context=ctx, timeout=30)
    else:
        server = smtplib.SMTP(host, port, timeout=30)
        if settings.get("smtp_security") == "starttls":
            server.starttls(context=ctx)
    try:
        if settings.get("smtp_user"):
            server.login(settings["smtp_user"], settings.get("smtp_password", ""))
        server.send_message(msg, to_addrs=recipients)
    finally:
        server.quit()
