"""Pure-function tests: AEAT reference fingerprints, chain audit, QR, Facturae."""

from __future__ import annotations

import base64

import pytest
from lxml import etree

from verifactu_mcp import facturae, huella, qr

# Reference vectors from the AEAT document "Detalle de las especificaciones técnicas para generación de la huella
# o hash de los registros de facturación".
H1 = "3C464DAF61ACB827C65FDA19F352A4E3BDC2C640E9E9FC4CC058073F38F12F60"
H2 = "F7B94CFD8924EDFF273501B01EE5153E4CE8F259766F88CF6ACB8935802A2B97"
H3 = "177547C0D57AC74748561D054A9CEC14B4C4EA23D1BEFD6F2E69E3A388F90C68"
R1 = {"IDEmisorFactura": "89890001K", "NumSerieFactura": "12345678/G33", "FechaExpedicionFactura": "01-01-2024",
      "TipoFactura": "F1", "CuotaTotal": "12.35", "ImporteTotal": "123.45",
      "FechaHoraHusoGenRegistro": "2024-01-01T19:20:30+01:00", "Huella": H1}
R2 = {"IDEmisorFactura": "89890001K", "NumSerieFactura": "12345679/G34", "FechaExpedicionFactura": "01-01-2024",
      "TipoFactura": "F1", "CuotaTotal": "12.35", "ImporteTotal": "123.45",
      "FechaHoraHusoGenRegistro": "2024-01-01T19:20:35+01:00", "Huella": H2}
R3 = {"TipoRegistro": "anulacion", "IDEmisorFacturaAnulada": "89890001K", "NumSerieFacturaAnulada": "12345679/G34",
      "FechaExpedicionFacturaAnulada": "01-01-2024", "FechaHoraHusoGenRegistro": "2024-01-01T19:20:40+01:00",
      "Huella": H3}
CHAIN = [R1, R2, R3]


def test_aeat_vectors():
    assert huella.compute(huella.normalize_record(R1), "")["huella"] == H1
    assert huella.compute(huella.normalize_record(R2), H1)["huella"] == H2
    assert huella.compute(huella.normalize_record(R3), H2)["huella"] == H3
    assert huella.compute(huella.normalize_record(R1), "")["cadena"].endswith(
        "&Huella=&FechaHoraHusoGenRegistro=2024-01-01T19:20:30+01:00")


def test_valid_chain():
    rep = huella.validate_chain(CHAIN)
    assert rep["valid"] and rep["errors"] == 0, rep["issues"]
    assert [r["estado"] for r in rep["results"]] == ["correcta"] * 3
    assert rep["last_huella"] == H3


def test_aliases_and_trim():
    rec = {"nif": " 89890001K ", "numserie": "12345678/G33", "issue_date": "01-01-2024", "invoice_type": "F1",
           "tax_total": "12.35", "total": "123.45", "generated_at": "2024-01-01T19:20:30+01:00"}
    assert huella.compute(huella.normalize_record(rec), "")["huella"] == H1


def test_tampered_amount_detected():
    bad = [R1, {**R2, "ImporteTotal": "1234.50"}, R3]
    rep = huella.validate_chain(bad)
    assert not rep["valid"]
    assert rep["results"][1]["estado"] == "incorrecta"
    # The next record still links to the stored fingerprint, so only record 2 is flagged.
    assert rep["results"][2]["estado"] == "correcta"


def test_break_in_chain():
    r2 = {**R2, "HuellaAnterior": "0" * 64}
    rep = huella.validate_chain([R1, r2])
    assert any("rotura de cadena" in i["message"] for i in rep["issues"])


def test_registro_anterior_mismatch_and_primer_registro():
    r2 = {**R2, "PrimerRegistro": "S",
          "RegistroAnterior": {"IDEmisorFactura": "89890001K", "NumSerieFactura": "OTRA",
                               "FechaExpedicionFactura": "01-01-2024", "Huella": H1}}
    rep = huella.validate_chain([R1, r2])
    msgs = " ".join(i["message"] for i in rep["issues"])
    assert "no identifica al registro previo" in msgs and "PrimerRegistro=S" in msgs


def test_partial_chain_and_continues_from():
    rep = huella.validate_chain([{**R2, "HuellaAnterior": H1}])
    assert rep["valid"] and any("cadena parcial" in i["message"] for i in rep["issues"])
    rep = huella.validate_chain([R2, R3], continues_from=H1)
    assert rep["valid"] and rep["warnings"] == 0, rep["issues"]


def test_field_errors():
    rec = {**R1, "IDEmisorFactura": "89890001A", "FechaExpedicionFactura": "2024-01-01", "TipoFactura": "F9",
           "ImporteTotal": "123,45", "FechaHoraHusoGenRegistro": "2024-01-01 19:20:30"}
    fields = {i["field"] for i in huella.field_issues(huella.normalize_record(rec))}
    assert {"IDEmisorFactura", "FechaExpedicionFactura", "TipoFactura", "ImporteTotal",
            "FechaHoraHusoGenRegistro"} <= fields


def test_trailing_zero_mismatch_is_warning():
    r = {**R1, "CuotaTotal": "12.350"}  # not valid in XML but shows the normalisation fallback
    r["CuotaTotal"] = "12.3"
    stored = huella.compute(huella.normalize_record(r), "")["huella"]
    rep = huella.validate_chain([{**r, "CuotaTotal": "12.30", "Huella": stored}])
    assert rep["results"][0]["estado"] == "correcta_sin_ceros" and rep["valid"]


def test_duplicate_and_unknown_cancellation():
    rep = huella.validate_chain([R1, {**R1, "Huella": ""}, {**R3, "NumSerieFacturaAnulada": "X1", "Huella": ""}])
    msgs = " ".join(i["message"] for i in rep["issues"])
    assert "ya registrada" in msgs and "no aparece en el lote" in msgs


def _xml(records: list[dict]) -> bytes:
    sf = "https://www2.agenciatributaria.gob.es/static_files/common/internet/dep/aplicaciones/es/aeat/tike/cont/" \
         "ws/SuministroInformacion.xsd"
    lr = sf.replace("SuministroInformacion", "SuministroLR")
    root = etree.Element(f"{{{lr}}}RegFactuSistemaFacturacion", nsmap={"sum": lr, "sum1": sf})
    cab = etree.SubElement(root, f"{{{lr}}}Cabecera")
    oe = etree.SubElement(cab, f"{{{sf}}}ObligadoEmision")
    etree.SubElement(oe, f"{{{sf}}}NombreRazon").text = "EMPRESA PRUEBA SL"
    etree.SubElement(oe, f"{{{sf}}}NIF").text = "89890001K"
    prev = None
    for r in records:
        reg = etree.SubElement(root, f"{{{lr}}}RegistroFactura")
        S = lambda p, t, x=None: (lambda e: (setattr(e, "text", x) if x is not None else None, e)[1])(  # noqa: E731
            etree.SubElement(p, f"{{{sf}}}{t}"))
        alta = r.get("TipoRegistro") != "anulacion"
        el = S(reg, "RegistroAlta" if alta else "RegistroAnulacion")
        S(el, "IDVersion", "1.0")
        idf = S(el, "IDFactura")
        names = huella.ALTA_FIELDS[:3] if alta else huella.ANUL_FIELDS
        for n in names:
            S(idf, n, r[n])
        if alta:
            S(el, "NombreRazonEmisor", "EMPRESA PRUEBA SL")
            S(el, "TipoFactura", r["TipoFactura"])
            S(el, "DescripcionOperacion", "Venta")
            det = S(S(el, "Desglose"), "DetalleDesglose")
            S(det, "ClaveRegimen", "01")
            S(det, "CalificacionOperacion", "S1")
            S(det, "TipoImpositivo", "10")
            S(det, "BaseImponibleOimporteNoSujeto", "111.10")
            S(det, "CuotaRepercutida", "12.35")
            S(el, "CuotaTotal", r["CuotaTotal"])
            S(el, "ImporteTotal", r["ImporteTotal"])
        enc = S(el, "Encadenamiento")
        if prev is None:
            S(enc, "PrimerRegistro", "S")
        else:
            ant = S(enc, "RegistroAnterior")
            pn = huella.ALTA_FIELDS[:3]
            for n in pn:
                S(ant, n, prev[n])
            S(ant, "Huella", prev["Huella"])
        si = S(el, "SistemaInformatico")
        for t, v in (("NombreRazon", "SOFT SL"), ("NIF", "89890001K"), ("NombreSistemaInformatico", "SIF"),
                     ("IdSistemaInformatico", "01"), ("Version", "1.0"), ("NumeroInstalacion", "1"),
                     ("TipoUsoPosibleSoloVerifactu", "S"), ("TipoUsoPosibleMultiOT", "N"),
                     ("IndicadorMultiplesOT", "N")):
            S(si, t, v)
        S(el, "FechaHoraHusoGenRegistro", r["FechaHoraHusoGenRegistro"])
        S(el, "TipoHuella", "01")
        S(el, "Huella", r["Huella"])
        if alta:
            prev = r
    return etree.tostring(root, xml_declaration=True, encoding="UTF-8")


AEAT_XML = _xml([R1, R2])


def test_parse_xml():
    _, recs = huella.parse_xml(AEAT_XML)
    assert [r["TipoRegistro"] for r in recs] == ["alta", "alta"]
    assert recs[1]["RegistroAnterior"]["Huella"] == H1
    rep = huella.validate_chain(recs)
    assert rep["valid"], rep["issues"]


def test_xxe_not_resolved():
    evil = (b'<?xml version="1.0"?><!DOCTYPE r [<!ENTITY x SYSTEM "file:///etc/passwd">]>'
            b'<r><RegistroAlta><Huella>&x;</Huella></RegistroAlta></r>')
    _, recs = huella.parse_xml(evil)
    assert "root:" not in recs[0]["Huella"]


# --- QR ---------------------------------------------------------------------------------------------------------

def test_qr_url():
    url = qr.build_url("89890001K", "12345678&G33", "2024-09-01", "241.4")
    assert url == ("https://www2.agenciatributaria.gob.es/wlpl/TIKE-CONT/ValidarQR?nif=89890001K"
                   "&numserie=12345678%26G33&fecha=01-09-2024&importe=241.4")
    test = qr.build_url("89890001K", "A 1/2", "01/09/2024", "10", verifactu=False, environment="pruebas")
    assert test.startswith("https://prewww2.aeat.es/wlpl/TIKE-CONT/ValidarQRNoVerifactu?")
    assert "numserie=A%201%2F2" in test
    parsed = qr.parse_url(url)
    assert parsed["valid"] and parsed["verifactu"] is True and parsed["params"]["numserie"] == "12345678&G33"


def test_qr_rejects_bad_input():
    for args in (("89890001A", "1", "01-01-2024", "1"), ("89890001K", "1", "2024-13-01", "1"),
                 ("89890001K", "1", "01-01-2024", "1.234")):
        with pytest.raises(huella.HuellaError):
            qr.build_url(*args)
    assert not qr.parse_url("https://evil.example/wlpl/TIKE-CONT/ValidarQR?nif=1")["valid"]


def test_qr_image():
    img = qr.render(qr.build_url("89890001K", "F-1", "01-01-2026", "121.00"), 300)
    raw = base64.b64decode(img["image_base64"])
    assert raw[:8] == b"\x89PNG\r\n\x1a\n" and img["error_correction"] == "M"


# --- Facturae ---------------------------------------------------------------------------------------------------

SELLER = {"nif": "B12345674", "name": "Gestoría Ejemplo SL", "address": "Calle Mayor 1", "post_code": "28013",
          "town": "Madrid", "province": "Madrid"}
PERSON = {"nif": "12345678Z", "name": "Ana", "first_surname": "López", "address": "C/ Sol 2",
          "post_code": "08001", "town": "Barcelona", "province": "Barcelona"}
INVOICE = {"number": "0001", "series": "A", "issue_date": "2026-11-05", "seller": SELLER, "buyer": PERSON,
           "lines": [{"description": "Servicios contables octubre", "quantity": 1, "unit_price": "300",
                      "tax_rate": 21},
                     {"description": "Modelo 303", "quantity": "2", "unit_price": "45,50", "tax_rate": 21,
                      "discount_pct": 10}],
           "withholding": {"rate": 15}, "payment": {"due_date": "2026-12-05",
                                                    "iban": "ES91 2100 0418 4502 0005 1332"}}


def test_build_facturae_valid_and_totals():
    out = facturae.build(INVOICE)
    assert out["valid"] and out["schema_valid"], out["issues"]
    t = out["totals"]
    assert (t["base_imponible"], t["cuota_impuestos"], t["retencion"], t["total_factura"]) == \
        ("381.90", "80.20", "57.29", "404.81")
    assert "<Individual>" in out["xml"] and "<IBAN>ES9121000418450200051332</IBAN>" in out["xml"]


def test_build_facturae_face_mixed_rates_and_surcharge():
    buyer = {"nif": "S2800000H", "name": "Ayuntamiento de Ejemplo", "address": "Plaza 1", "post_code": "28001",
             "town": "Madrid", "province": "Madrid",
             "face": {"oficina_contable": "L01280796", "organo_gestor": "L01280796", "unidad_tramitadora": "L01280796"}}
    inv = {**INVOICE, "buyer": buyer, "withholding": None,
           "lines": [{"description": "A", "quantity": 3, "unit_price": "10.333", "tax_rate": 10},
                     {"description": "B", "quantity": 1, "unit_price": "50", "tax_rate": 21, "surcharge_rate": "5.2"},
                     {"description": "C", "quantity": 1, "unit_price": "20", "tax_rate": 4}]}
    out = facturae.build(inv)
    assert out["valid"], out["issues"]
    assert out["xml"].count("<RoleTypeCode>") == 3
    assert len(out["totals"]["desglose"]) == 3
    assert out["totals"]["total_factura"] == "118.00"  # 31.00+3.10 + 50+10.50+2.60 + 20+0.80


def test_build_facturae_foreign_buyer():
    buyer = {"nif": "FR12345678901", "name": "Client SARL", "address": "1 rue X", "post_code": "75001",
             "town": "Paris", "province": "Paris", "country": "FRA", "eu": True}
    out = facturae.build({**INVOICE, "buyer": buyer, "withholding": None,
                          "lines": [{"description": "Servicio", "quantity": 1, "unit_price": 100, "tax_rate": 0}]})
    assert out["valid"], out["issues"]
    assert "<ResidenceTypeCode>U</ResidenceTypeCode>" in out["xml"] and "<OverseasAddress>" in out["xml"]


def test_build_facturae_rejects():
    for bad in ({**INVOICE, "seller": {**SELLER, "nif": "B12345670"}}, {**INVOICE, "lines": []},
                {**INVOICE, "issue_date": "ayer"}, {**INVOICE, "number": "X" * 21}):
        with pytest.raises(facturae.FacturaeError):
            facturae.build(bad)


def test_validate_facturae_catches_arithmetic():
    xml = facturae.build(INVOICE)["xml"].replace("<InvoiceTotal>404.81</InvoiceTotal>",
                                                 "<InvoiceTotal>405.81</InvoiceTotal>")
    rep = facturae.validate(xml.encode())
    assert not rep["valid"] and rep["schema_valid"]
    assert any("InvoiceTotal" in i["message"] for i in rep["issues"])


def test_validate_facturae_schema_error():
    xml = facturae.build(INVOICE)["xml"].replace("<InvoiceClass>OO</InvoiceClass>", "<InvoiceClass>ZZ</InvoiceClass>")
    rep = facturae.validate(xml.encode())
    assert rep["schema_valid"] is False and rep["schema_errors"]


def test_validate_facturae_not_facturae():
    with pytest.raises(facturae.FacturaeError):
        facturae.validate(b"<foo/>")
