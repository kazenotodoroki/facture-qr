"""Références de paiement QR : QRR (avec QR-IBAN) ou SCOR / RF (avec IBAN normal)."""
import re
from stdnum.iso7064 import mod_97_10

_MOD10_TABLE = [0, 9, 4, 6, 8, 2, 7, 1, 3, 5]


def _clean_iban(iban: str) -> str:
    return re.sub(r"\s+", "", iban or "").upper()


def is_qr_iban(iban: str) -> bool:
    """Un QR-IBAN a un IID (positions 5-9) entre 30000 et 31999."""
    iban = _clean_iban(iban)
    if len(iban) < 9 or iban[:2] not in ("CH", "LI"):
        return False
    try:
        return 30000 <= int(iban[4:9]) <= 31999
    except ValueError:
        return False


def _mod10_recursive(digits: str) -> str:
    carry = 0
    for d in digits:
        carry = _MOD10_TABLE[(carry + int(d)) % 10]
    return str((10 - carry) % 10)


def make_reference(iban: str, invoice_number: str, invoice_id: int) -> str:
    """Construit une référence structurée à partir du n° de facture."""
    digits = re.sub(r"\D", "", invoice_number) or str(invoice_id)
    if is_qr_iban(iban):
        base = digits.zfill(26)[-26:]
        return base + _mod10_recursive(base)
    body = digits[-21:]
    return "RF" + mod_97_10.calc_check_digits(body + "RF") + body


def format_reference(ref: str) -> str:
    if not ref:
        return ""
    if ref.startswith("RF"):
        return " ".join(ref[i:i + 4] for i in range(0, len(ref), 4))
    # QRR : 2 + groupes de 5
    return ref[:2] + " " + " ".join(ref[i:i + 5] for i in range(2, len(ref), 5))
