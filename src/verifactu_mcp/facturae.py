"""Facturae 3.2.2: build an unsigned invoice from plain data and validate files against the official XSD.

Schema: Facturaev3_2_2.xsd (Ministerio de Hacienda / facturae.gob.es), bundled in ``schemas/`` with the W3C
xmldsig schema it imports. The XML produced here is unsigned: FACe and most B2B platforms require a XAdES
signature with the issuer's certificate, applied afterwards (AutoFirma, the platform, or your signing library).
"""

from __future__ import annotations

import uuid
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from functools import lru_cache
from importlib import resources
from typing import Any

from lxml import etree

from . import ids

NS = "http://www.facturae.gob.es/formato/Versiones/Facturaev3_2_2.xml"
DS = "http://www.w3.org/2000/09/xmldsig#"
TAX_TYPES = {"01": "IVA", "02": "IPSI", "03": "IGIC", "04": "IRPF", "05": "Otro", "06": "ITPAJD", "07": "IE",
             "08": "RA", "09": "IGTECM", "10": "IECDPCAC", "11": "IIIMAB", "12": "ICIO", "13": "IMVDN",
             "14": "IMSN", "15": "IMGSN", "16": "IMPN", "17": "REIVA", "18": "REIGIC", "19": "REIPSI",
             "20": "IPS", "21": "RLEA", "22": "IVPEE", "23": "IPCNG", "24": "IACNG", "25": "IDEC", "26": "ILTCAC",
             "27": "IGFEI", "28": "IRNR", "29": "ISS"}
FACE_ROLES = {"01": "Oficina contable", "02": "Órgano gestor", "03": "Unidad tramitadora", "04": "Órgano proponente"}
PAYMENT_MEANS = {"transferencia": "04", "domiciliacion": "02", "efectivo": "01", "cheque": "11", "tarjeta": "19",
                 "contrareembolso": "03", "recibo": "05"}
Q2 = Decimal("0.01")
MAX_LINES = 5000


class FacturaeError(ValueError):
    pass


def d(value: Any, field: str) -> Decimal:
    if value is None or value == "":
        raise FacturaeError(f"{field}: valor obligatorio")
    s = str(value).strip()
    if "," in s:
        s = s.replace(".", "").replace(",", ".")  # 1.234,56 -> 1234.56
    try:
        out = Decimal(s)
    except InvalidOperation as exc:
        raise FacturaeError(f"{field}: número no válido {value!r}") from exc
    if not out.is_finite():
        raise FacturaeError(f"{field}: número no válido {value!r}")
    return out


def r2(x: Decimal) -> Decimal:
    return x.quantize(Q2, rounding=ROUND_HALF_UP)


def f2(x: Decimal) -> str:
    return f"{r2(x):.2f}"


def fq(x: Decimal) -> str:
    """Up to 8 decimals, no exponent, at least one decimal place kept out (xs:double pattern)."""
    s = f"{x.quantize(Decimal('0.00000001'), rounding=ROUND_HALF_UP):f}".rstrip("0").rstrip(".")
    return s


def _date(value: Any, field: str) -> str:
    from .huella import to_aeat_date

    try:
        dd, mm, yyyy = to_aeat_date(str(value)).split("-")
    except ValueError as exc:
        raise FacturaeError(f"{field}: {exc}") from exc
    return f"{yyyy}-{mm}-{dd}"


@lru_cache(maxsize=1)
def schema() -> etree.XMLSchema:
    base = resources.files("verifactu_mcp") / "schemas" / "Facturaev3_2_2.xsd"
    with resources.as_file(base) as path:
        return etree.XMLSchema(etree.parse(str(path)))


def _sub(parent: Any, tag: str, text: Any = None) -> Any:
    el = etree.SubElement(parent, tag)
    if text is not None:
        el.text = str(text)
    return el


def _party(parent: Any, tag: str, p: dict[str, Any], role: str) -> None:
    if not isinstance(p, dict):
        raise FacturaeError(f"{role}: objeto obligatorio")
    nif = str(p.get("nif") or "").strip().upper().replace(" ", "").replace("-", "")
    if not nif:
        raise FacturaeError(f"{role}.nif obligatorio")
    country = str(p.get("country") or "ESP").upper()
    residence = str(p.get("residence") or ("R" if country == "ESP" else "U" if p.get("eu") else "E")).upper()
    if residence not in ("R", "U", "E"):
        raise FacturaeError(f"{role}.residence debe ser R (residente), U (UE) o E (extranjero)")
    check = ids.validate_nif(nif) if residence == "R" else {"valid": True}
    if residence == "R" and not check["valid"]:
        raise FacturaeError(f"{role}.nif {nif!r} no es un NIF español válido")
    individual = bool(p.get("first_surname"))
    person = str(p.get("person_type") or ("F" if individual or check.get("type") in ("DNI", "NIE") else "J"))
    biz = _sub(parent, tag)
    tid = _sub(biz, "TaxIdentification")
    _sub(tid, "PersonTypeCode", person)
    _sub(tid, "ResidenceTypeCode", residence)
    _sub(tid, "TaxIdentificationNumber", nif)

    centres = list(p.get("administrative_centres") or [])
    face = p.get("face") or {}
    for key, rc in (("oficina_contable", "01"), ("organo_gestor", "02"), ("unidad_tramitadora", "03"),
                    ("organo_proponente", "04")):
        if face.get(key):
            centres.append({"code": face[key], "role": rc})
    addr = _address_fields(p, role)
    if centres:
        acs = _sub(biz, "AdministrativeCentres")
        for c in centres:
            ac = _sub(acs, "AdministrativeCentre")
            code = str(c.get("code") or "").strip().upper()
            if not code or len(code) > 10:
                raise FacturaeError(f"{role}: código DIR3 de centro administrativo obligatorio (máx. 10)")
            rc = str(c.get("role") or "")
            if rc not in FACE_ROLES:
                raise FacturaeError(f"{role}: role de centro {rc!r} no válido ({', '.join(FACE_ROLES)})")
            _sub(ac, "CentreCode", code)
            _sub(ac, "RoleTypeCode", rc)
            if c.get("name"):
                _sub(ac, "Name", str(c["name"])[:40])
            _write_address(ac, c.get("address") and _address_fields(c["address"], role) or addr)
            if c.get("description"):
                _sub(ac, "CentreDescription", str(c["description"])[:2500])
    if individual:
        ent = _sub(biz, "Individual")
        _sub(ent, "Name", str(p.get("name") or "")[:40] or _missing(f"{role}.name"))
        _sub(ent, "FirstSurname", str(p["first_surname"])[:40])
        if p.get("second_surname"):
            _sub(ent, "SecondSurname", str(p["second_surname"])[:40])
    else:
        ent = _sub(biz, "LegalEntity")
        _sub(ent, "CorporateName", str(p.get("name") or "")[:80] or _missing(f"{role}.name"))
        if p.get("trade_name"):
            _sub(ent, "TradeName", str(p["trade_name"])[:40])
    _write_address(ent, addr)
    if p.get("email") or p.get("phone"):
        cd = _sub(ent, "ContactDetails")
        if p.get("phone"):
            _sub(cd, "Telephone", str(p["phone"])[:15])
        if p.get("email"):
            _sub(cd, "ElectronicMail", str(p["email"])[:60])


def _missing(field: str) -> str:
    raise FacturaeError(f"{field} obligatorio")


def _address_fields(p: dict[str, Any], role: str) -> dict[str, str]:
    out = {k: str(p.get(k) or "").strip() for k in ("address", "post_code", "town", "province")}
    out["country"] = str(p.get("country") or "ESP").upper()
    for k in ("address", "post_code", "town", "province"):
        if not out[k]:
            raise FacturaeError(f"{role}.{k} obligatorio")
    if len(out["country"]) != 3:
        raise FacturaeError(f"{role}.country: código ISO 3166-1 alfa-3 (ESP, FRA, PRT...)")
    if out["country"] == "ESP" and not (out["post_code"].isdigit() and len(out["post_code"]) == 5):
        raise FacturaeError(f"{role}.post_code: código postal español de 5 dígitos")
    return out


def _write_address(parent: Any, a: dict[str, str]) -> None:
    if a["country"] == "ESP":
        el = _sub(parent, "AddressInSpain")
        _sub(el, "Address", a["address"][:80])
        _sub(el, "PostCode", a["post_code"])
        _sub(el, "Town", a["town"][:50])
        _sub(el, "Province", a["province"][:20])
        _sub(el, "CountryCode", "ESP")
    else:
        el = _sub(parent, "OverseasAddress")
        _sub(el, "Address", a["address"][:80])
        _sub(el, "PostCodeAndTown", f"{a['post_code']} {a['town']}"[:50])
        _sub(el, "Province", a["province"][:20])
        _sub(el, "CountryCode", a["country"])


def _tax(parent: Any, code: str, rate: Decimal, base: Decimal, amount: Decimal,
         re_rate: Decimal | None = None, re_amount: Decimal | None = None) -> None:
    t = _sub(parent, "Tax")
    _sub(t, "TaxTypeCode", code)
    _sub(t, "TaxRate", f2(rate))
    _sub(_sub(t, "TaxableBase"), "TotalAmount", f2(base))
    _sub(_sub(t, "TaxAmount"), "TotalAmount", f2(amount))
    if re_rate is not None:
        _sub(t, "EquivalenceSurcharge", f2(re_rate))
        _sub(_sub(t, "EquivalenceSurchargeAmount"), "TotalAmount", f2(re_amount or Decimal(0)))


def build(inv: dict[str, Any]) -> dict[str, Any]:
    """Return {'xml': str, 'totals': {...}, 'schema_valid': bool, 'schema_errors': [...]}."""
    lines_in = inv.get("lines") or []
    if not lines_in:
        raise FacturaeError("lines: la factura necesita al menos una línea")
    if len(lines_in) > MAX_LINES:
        raise FacturaeError(f"máximo {MAX_LINES} líneas")
    number = str(inv.get("number") or "").strip()
    if not number or len(number) > 20:
        raise FacturaeError("number obligatorio (máx. 20 caracteres; la serie va en 'series')")
    series = str(inv.get("series") or "").strip()
    if len(series) > 20:
        raise FacturaeError("series: máximo 20 caracteres")
    currency = str(inv.get("currency") or "EUR").upper()
    issue = _date(inv.get("issue_date"), "issue_date")
    tax_code = str(inv.get("tax_type") or "01")
    if tax_code not in TAX_TYPES:
        raise FacturaeError(f"tax_type {tax_code!r} no válido")

    lines: list[dict[str, Any]] = []
    groups: dict[tuple[str, Decimal, Decimal | None], dict[str, Decimal]] = {}
    for i, ln in enumerate(lines_in, 1):
        qty = d(ln.get("quantity", 1), f"lines[{i}].quantity")
        price = d(ln.get("unit_price"), f"lines[{i}].unit_price")
        rate = d(ln.get("tax_rate", 21), f"lines[{i}].tax_rate")
        code = str(ln.get("tax_type") or tax_code)
        if code not in TAX_TYPES or code == "04":
            raise FacturaeError(f"lines[{i}].tax_type {code!r} no válido para impuesto repercutido")
        re_rate = d(ln["surcharge_rate"], f"lines[{i}].surcharge_rate") if ln.get("surcharge_rate") else None
        desc = str(ln.get("description") or "").strip()
        if not desc:
            raise FacturaeError(f"lines[{i}].description obligatoria")
        total_cost = r2(qty * price)
        disc_pct = d(ln["discount_pct"], f"lines[{i}].discount_pct") if ln.get("discount_pct") else None
        disc = r2(total_cost * disc_pct / 100) if disc_pct else Decimal(0)
        gross = total_cost - disc
        tax_amt = r2(gross * rate / 100)
        re_amt = r2(gross * re_rate / 100) if re_rate is not None else None
        lines.append({"desc": desc, "qty": qty, "price": price, "total_cost": total_cost, "disc_pct": disc_pct,
                      "disc": disc, "gross": gross, "rate": rate, "code": code, "tax": tax_amt, "re_rate": re_rate,
                      "re": re_amt, "unit": str(ln.get("unit") or "01"), "ref": ln.get("receiver_reference"),
                      "file_ref": ln.get("file_reference"), "article": ln.get("article_code")})
        g = groups.setdefault((code, rate, re_rate), {"base": Decimal(0)})
        g["base"] += gross

    for (code, rate, re_rate), g in groups.items():
        g["tax"] = r2(g["base"] * rate / 100)
        g["re"] = r2(g["base"] * re_rate / 100) if re_rate is not None else Decimal(0)

    gross_total = sum((ln["gross"] for ln in lines), Decimal(0))
    tax_total = sum((g["tax"] + g["re"] for g in groups.values()), Decimal(0))
    wh = inv.get("withholding") or {}
    wh_rate = d(wh["rate"], "withholding.rate") if wh.get("rate") else None
    wh_amount = r2(gross_total * wh_rate / 100) if wh_rate else Decimal(0)
    invoice_total = gross_total + tax_total - wh_amount
    outstanding = invoice_total

    root = etree.Element(f"{{{NS}}}Facturae", nsmap={"fe": NS, "ds": DS})
    fh = _sub(root, "FileHeader")
    _sub(fh, "SchemaVersion", "3.2.2")
    _sub(fh, "Modality", "I")
    _sub(fh, "InvoiceIssuerType", "EM")
    b = _sub(fh, "Batch")
    seller_nif = str((inv.get("seller") or {}).get("nif") or "").upper().strip()
    _sub(b, "BatchIdentifier", f"{seller_nif}{series}{number}"[:70])
    _sub(b, "InvoicesCount", "1")
    for tag, val in (("TotalInvoicesAmount", invoice_total), ("TotalOutstandingAmount", outstanding),
                     ("TotalExecutableAmount", outstanding)):
        _sub(_sub(b, tag), "TotalAmount", f2(val))
    _sub(b, "InvoiceCurrencyCode", currency)

    parties = _sub(root, "Parties")
    _party(parties, "SellerParty", inv.get("seller") or {}, "seller")
    _party(parties, "BuyerParty", inv.get("buyer") or {}, "buyer")

    invoice = _sub(_sub(root, "Invoices"), "Invoice")
    ih = _sub(invoice, "InvoiceHeader")
    _sub(ih, "InvoiceNumber", number)
    if series:
        _sub(ih, "InvoiceSeriesCode", series)
    _sub(ih, "InvoiceDocumentType", "FC")
    _sub(ih, "InvoiceClass", "OO")
    idata = _sub(invoice, "InvoiceIssueData")
    _sub(idata, "IssueDate", issue)
    if inv.get("operation_date"):
        _sub(idata, "OperationDate", _date(inv["operation_date"], "operation_date"))
    _sub(idata, "InvoiceCurrencyCode", currency)
    _sub(idata, "TaxCurrencyCode", "EUR")
    _sub(idata, "LanguageName", str(inv.get("language") or "es"))
    if inv.get("description"):
        _sub(idata, "InvoiceDescription", str(inv["description"])[:2500])
    if inv.get("receiver_contract_reference"):
        _sub(idata, "ReceiverContractReference", str(inv["receiver_contract_reference"])[:20])

    tos = _sub(invoice, "TaxesOutputs")
    for (code, rate, re_rate), g in groups.items():
        _tax(tos, code, rate, g["base"], g["tax"], re_rate, g["re"] if re_rate is not None else None)
    if wh_rate:
        tw = _sub(invoice, "TaxesWithheld")
        t = _sub(tw, "Tax")
        _sub(t, "TaxTypeCode", "04")
        _sub(t, "TaxRate", f2(wh_rate))
        _sub(_sub(t, "TaxableBase"), "TotalAmount", f2(gross_total))
        _sub(_sub(t, "TaxAmount"), "TotalAmount", f2(wh_amount))
    tt = _sub(invoice, "InvoiceTotals")
    _sub(tt, "TotalGrossAmount", f2(gross_total))
    _sub(tt, "TotalGrossAmountBeforeTaxes", f2(gross_total))
    _sub(tt, "TotalTaxOutputs", f2(tax_total))
    _sub(tt, "TotalTaxesWithheld", f2(wh_amount))
    _sub(tt, "InvoiceTotal", f2(invoice_total))
    _sub(tt, "TotalOutstandingAmount", f2(outstanding))
    _sub(tt, "TotalExecutableAmount", f2(outstanding))

    items = _sub(invoice, "Items")
    for ln in lines:
        il = _sub(items, "InvoiceLine")
        if ln["ref"]:
            _sub(il, "ReceiverContractReference", str(ln["ref"])[:20])
        if ln["file_ref"]:
            _sub(il, "FileReference", str(ln["file_ref"])[:20])
        _sub(il, "ItemDescription", ln["desc"][:2500])
        _sub(il, "Quantity", fq(ln["qty"]))
        _sub(il, "UnitOfMeasure", ln["unit"])
        _sub(il, "UnitPriceWithoutTax", fq(ln["price"]))
        _sub(il, "TotalCost", f2(ln["total_cost"]))
        if ln["disc"]:
            dr = _sub(_sub(il, "DiscountsAndRebates"), "Discount")
            _sub(dr, "DiscountReason", "Descuento")
            _sub(dr, "DiscountRate", fq(ln["disc_pct"]))
            _sub(dr, "DiscountAmount", f2(ln["disc"]))
        _sub(il, "GrossAmount", f2(ln["gross"]))
        if wh_rate:
            t = _sub(_sub(il, "TaxesWithheld"), "Tax")
            _sub(t, "TaxTypeCode", "04")
            _sub(t, "TaxRate", f2(wh_rate))
            _sub(_sub(t, "TaxableBase"), "TotalAmount", f2(ln["gross"]))
            _sub(_sub(t, "TaxAmount"), "TotalAmount", f2(r2(ln["gross"] * wh_rate / 100)))
        lto = _sub(il, "TaxesOutputs")
        _tax(lto, ln["code"], ln["rate"], ln["gross"], ln["tax"], ln["re_rate"], ln["re"])
        if ln["article"]:
            _sub(il, "ArticleCode", str(ln["article"])[:20])

    pay = inv.get("payment") or {}
    if pay:
        inst = _sub(_sub(invoice, "PaymentDetails"), "Installment")
        _sub(inst, "InstallmentDueDate", _date(pay.get("due_date") or inv.get("issue_date"), "payment.due_date"))
        _sub(inst, "InstallmentAmount", f2(outstanding))
        means = str(pay.get("method") or "transferencia").lower()
        means = PAYMENT_MEANS.get(means, means)
        if means not in PAYMENT_MEANS.values():
            raise FacturaeError(f"payment.method no válido ({', '.join(PAYMENT_MEANS)})")
        _sub(inst, "PaymentMeans", means)
        if pay.get("iban"):
            check = ids.validate_iban(str(pay["iban"]))
            if not check.get("valid"):
                raise FacturaeError("payment.iban no válido")
            acct = _sub(inst, "AccountToBeCredited")
            _sub(acct, "IBAN", check.get("normalized") or str(pay["iban"]).replace(" ", ""))
            if pay.get("bic"):
                _sub(acct, "BIC", str(pay["bic"]).upper())
    if inv.get("legal_literals"):
        ll = _sub(invoice, "LegalLiterals")
        for text in inv["legal_literals"][:20]:
            _sub(ll, "LegalReference", str(text)[:250])
    if inv.get("notes"):
        _sub(_sub(invoice, "AdditionalData"), "InvoiceAdditionalInformation", str(inv["notes"])[:2500])

    # The schema is elementFormDefault="unqualified": only the root element carries the Facturae namespace.
    xml = etree.tostring(root, xml_declaration=True, encoding="UTF-8", pretty_print=True)
    report = validate(xml)
    totals = {"base_imponible": f2(gross_total), "cuota_impuestos": f2(tax_total), "retencion": f2(wh_amount),
              "total_factura": f2(invoice_total), "total_a_pagar": f2(outstanding),
              "desglose": [{"impuesto": TAX_TYPES[c], "tipo": f2(r), "base": f2(g["base"]), "cuota": f2(g["tax"]),
                            **({"recargo_tipo": f2(rr), "recargo_cuota": f2(g["re"])} if rr is not None else {})}
                           for (c, r, rr), g in groups.items()]}
    return {"xml": xml.decode("utf-8"), "totals": totals, **report}


# --- validation -----------------------------------------------------------------------------------------------

def _num(el: Any, path: str) -> Decimal | None:
    found = el.find(path)
    if found is None or not (found.text or "").strip():
        return None
    try:
        return Decimal(found.text.strip())
    except InvalidOperation:
        return None


def validate(data: bytes) -> dict[str, Any]:
    parser = etree.XMLParser(resolve_entities=False, no_network=True, huge_tree=False, remove_comments=True)
    try:
        root = etree.fromstring(data, parser)
    except etree.XMLSyntaxError as exc:
        raise FacturaeError(f"XML mal formado: {exc}") from exc
    if etree.QName(root).localname != "Facturae":
        raise FacturaeError("no es un fichero Facturae (raíz distinta de <Facturae>)")
    ns = etree.QName(root).namespace or ""
    issues: list[dict[str, str]] = []

    def add(level: str, msg: str, where: str = "") -> None:
        issues.append({"level": level, "where": where, "message": msg})

    version = (root.findtext("FileHeader/SchemaVersion") or "").strip()
    schema_valid = None
    schema_errors: list[str] = []
    if ns != NS:
        add("error", f"espacio de nombres {ns!r}: solo se valida Facturae 3.2.2 ({NS})")
    else:
        sch = schema()
        schema_valid = sch.validate(etree.ElementTree(root))
        for e in list(sch.error_log)[:50]:
            schema_errors.append(f"línea {e.line}: {e.message}")
            add("error", e.message, f"línea {e.line}")
    if version and version != "3.2.2":
        add("error", f"SchemaVersion {version!r}; se esperaba 3.2.2")

    tol = Q2
    invoices = root.findall("Invoices/Invoice")
    modality = (root.findtext("FileHeader/Modality") or "").strip()
    count = root.findtext("FileHeader/Batch/InvoicesCount")
    if count and count.strip().isdigit() and int(count) != len(invoices):
        add("error", f"InvoicesCount={count} pero el fichero contiene {len(invoices)} facturas", "Batch")
    if modality == "I" and len(invoices) != 1:
        add("error", "Modality I (individual) exige exactamente una factura", "FileHeader")
    sum_total = Decimal(0)
    sum_out = Decimal(0)
    sum_exec = Decimal(0)
    for party in ("SellerParty", "BuyerParty"):
        p = root.find(f"Parties/{party}/TaxIdentification")
        if p is None:
            continue
        nif = (p.findtext("TaxIdentificationNumber") or "").strip().upper()
        if (p.findtext("ResidenceTypeCode") or "").strip() == "R":
            chk = ids.validate_nif(nif)
            if not chk["valid"]:
                add("error", f"NIF {nif!r} no válido", party)
    buyer_centres = root.findall("Parties/BuyerParty/AdministrativeCentres/AdministrativeCentre")
    if buyer_centres:
        roles = {(c.findtext("RoleTypeCode") or "").strip() for c in buyer_centres}
        missing = [FACE_ROLES[r] for r in ("01", "02", "03") if r not in roles]
        if missing:
            add("warning", f"FACe exige los tres códigos DIR3; faltan: {', '.join(missing)}", "BuyerParty")

    for n, inv in enumerate(invoices, 1):
        where = f"factura {n} ({inv.findtext('InvoiceHeader/InvoiceNumber')})"
        gross_sum = Decimal(0)
        line_taxes: dict[tuple[str, Decimal], Decimal] = {}
        for k, il in enumerate(inv.findall("Items/InvoiceLine"), 1):
            lw = f"{where}, línea {k}"
            qty, price = _num(il, "Quantity"), _num(il, "UnitPriceWithoutTax")
            cost, gross = _num(il, "TotalCost"), _num(il, "GrossAmount")
            if None not in (qty, price, cost) and abs(r2(qty * price) - cost) > tol:
                add("error", f"TotalCost {cost} ≠ Quantity × UnitPriceWithoutTax ({r2(qty * price)})", lw)
            disc = sum((Decimal(x.text) for x in il.findall("DiscountsAndRebates/Discount/DiscountAmount")
                        if x.text), Decimal(0))
            chg = sum((Decimal(x.text) for x in il.findall("Charges/Charge/ChargeAmount") if x.text), Decimal(0))
            if cost is not None and gross is not None and abs(cost - disc + chg - gross) > tol:
                add("error", f"GrossAmount {gross} ≠ TotalCost − descuentos + cargos ({cost - disc + chg})", lw)
            gross_sum += gross or Decimal(0)
            for t in il.findall("TaxesOutputs/Tax"):
                base, amt, rate = _num(t, "TaxableBase/TotalAmount"), _num(t, "TaxAmount/TotalAmount"), \
                    _num(t, "TaxRate")
                if None not in (base, amt, rate) and abs(r2(base * rate / 100) - amt) > tol:
                    add("error", f"cuota {amt} ≠ base {base} × {rate}% ({r2(base * rate / 100)})", lw)
                key = ((t.findtext("TaxTypeCode") or "").strip(), rate or Decimal(0))
                line_taxes[key] = line_taxes.get(key, Decimal(0)) + (base or Decimal(0))

        out_total = Decimal(0)
        for t in inv.findall("TaxesOutputs/Tax"):
            base, amt, rate = _num(t, "TaxableBase/TotalAmount"), _num(t, "TaxAmount/TotalAmount"), _num(t, "TaxRate")
            re_amt = _num(t, "EquivalenceSurchargeAmount/TotalAmount") or Decimal(0)
            out_total += (amt or Decimal(0)) + re_amt
            if None not in (base, amt, rate) and abs(r2(base * rate / 100) - amt) > tol:
                add("error", f"cuota {amt} ≠ base {base} × {rate}% ({r2(base * rate / 100)})", where)
            key = ((t.findtext("TaxTypeCode") or "").strip(), rate or Decimal(0))
            if key in line_taxes and base is not None and abs(line_taxes[key] - base) > tol:
                add("error", f"base del {key[1]}% en cabecera ({base}) ≠ suma de líneas ({line_taxes[key]})", where)
        wh_total = sum((_num(t, "TaxAmount/TotalAmount") or Decimal(0)
                        for t in inv.findall("TaxesWithheld/Tax")), Decimal(0))
        tt = inv.find("InvoiceTotals")
        if tt is None:
            continue
        g = {k: _num(tt, k) for k in ("TotalGrossAmount", "TotalGeneralDiscounts", "TotalGeneralSurcharges",
                                       "TotalGrossAmountBeforeTaxes", "TotalTaxOutputs", "TotalTaxesWithheld",
                                       "InvoiceTotal", "TotalOutstandingAmount", "TotalExecutableAmount",
                                       "TotalPaymentsOnAccount", "TotalFinancialExpenses",
                                       "TotalReimbursableExpenses")}
        z = Decimal(0)

        def eq(a: Decimal | None, b: Decimal, label: str) -> None:
            if a is not None and abs(a - b) > tol:
                add("error", f"{label}: {a} ≠ {b}", where)

        eq(g["TotalGrossAmount"], gross_sum, "TotalGrossAmount ≠ suma de GrossAmount")
        if g["TotalGrossAmount"] is not None:
            eq(g["TotalGrossAmountBeforeTaxes"], g["TotalGrossAmount"] - (g["TotalGeneralDiscounts"] or z)
               + (g["TotalGeneralSurcharges"] or z), "TotalGrossAmountBeforeTaxes")
        eq(g["TotalTaxOutputs"], out_total, "TotalTaxOutputs ≠ suma de impuestos repercutidos")
        eq(g["TotalTaxesWithheld"], wh_total, "TotalTaxesWithheld ≠ suma de retenciones")
        if None not in (g["TotalGrossAmountBeforeTaxes"], g["TotalTaxOutputs"], g["TotalTaxesWithheld"]):
            eq(g["InvoiceTotal"], g["TotalGrossAmountBeforeTaxes"] + g["TotalTaxOutputs"] - g["TotalTaxesWithheld"],
               "InvoiceTotal ≠ base + impuestos − retenciones")
        if g["InvoiceTotal"] is not None:
            # Subsidies are not modelled; only flag the plain case.
            if inv.find("InvoiceTotals/Subsidies") is None:
                eq(g["TotalOutstandingAmount"], g["InvoiceTotal"] - (g["TotalPaymentsOnAccount"] or z),
                   "TotalOutstandingAmount ≠ InvoiceTotal − anticipos")
            sum_total += g["InvoiceTotal"]
        sum_out += g["TotalOutstandingAmount"] or z
        sum_exec += g["TotalExecutableAmount"] or z
        inst = sum((_num(i, "InstallmentAmount") or z for i in inv.findall("PaymentDetails/Installment")), z)
        if inv.find("PaymentDetails") is not None and g["TotalExecutableAmount"] is not None \
                and abs(inst - g["TotalExecutableAmount"]) > tol:
            add("warning", f"suma de vencimientos {inst} ≠ TotalExecutableAmount {g['TotalExecutableAmount']}", where)

    batch = root.find("FileHeader/Batch")
    if batch is not None and invoices:
        for tag, val in (("TotalInvoicesAmount", sum_total), ("TotalOutstandingAmount", sum_out),
                         ("TotalExecutableAmount", sum_exec)):
            got = _num(batch, f"{tag}/TotalAmount")
            if got is not None and abs(got - val) > tol:
                add("error", f"Batch/{tag} {got} ≠ suma de facturas {val}", "Batch")
    signed = root.find(f"{{{DS}}}Signature") is not None
    if not signed:
        add("warning", "factura sin firma XAdES: FACe y la mayoría de plataformas B2B exigen firmarla con el "
                       "certificado del emisor antes de enviarla", "Signature")
    errors = sum(1 for i in issues if i["level"] == "error")
    return {"valid": errors == 0, "schema_valid": schema_valid, "signed": signed, "invoices": len(invoices),
            "errors": errors, "issues": issues[:300], "schema_errors": schema_errors}
