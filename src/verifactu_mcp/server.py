"""VeriFactu MCP: audit VERI*FACTU fingerprint chains, generate the AEAT tax QR, and build/validate Facturae
3.2.2 invoices, for AI agents working for gestorías, accountants and invoicing software teams.

This server is a checking and generation aid. It is not an invoicing system (SIF), does not keep records and does
not send anything to the AEAT. Paid tools debit KeyFlint credits only after the input has been read successfully,
and refund them if the tool body fails. Helper tools are free.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import binascii
import os
from contextlib import contextmanager
from functools import lru_cache
from importlib import resources
from typing import Any, Iterator

from lxml import etree
from mcp.server.mcpserver import Context, MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations

from . import __version__, calendar, facturae, huella, ids, qr
from . import apps
from .billing import Billing

DEFAULT_PRICES = {"verifactu_hash": 1, "validate_invoice_chain": 3, "validate_verifactu_xml": 3,
                  "generate_verifactu_qr": 1, "build_facturae": 3, "validate_facturae": 2}
MAX_BYTES = int(os.environ.get("VERIFACTU_MAX_BYTES", 10_000_000))
PREVIEW = 200


def _prices() -> dict[str, int]:
    prices = dict(DEFAULT_PRICES)
    for item in filter(None, (p.strip() for p in os.environ.get("VERIFACTU_PRICES", "").split(","))):
        name, _, n = item.partition("=")
        if name.strip() in prices and n.strip().isdigit() and int(n) > 0:
            prices[name.strip()] = int(n)
    return prices


PRICES = _prices()
billing = Billing(hosted=os.environ.get("VERIFACTU_TRANSPORT", "stdio") != "stdio")
LOCAL = ToolAnnotations(readOnlyHint=True, openWorldHint=False, idempotentHint=True)

mcp = MCPServer(
    "verifactu-mcp",
    title="VeriFactu y Facturae: huella, QR tributario y factura electrónica",
    version=__version__,
    website_url="https://github.com/ArceeDev/verifactu-mcp",
    instructions=(
        "Spanish invoicing compliance checks, offline and stateless. `validate_invoice_chain` and "
        "`validate_verifactu_xml` recompute the SHA-256 fingerprint (huella) of every VERI*FACTU record (alta and "
        "anulación) exactly as the AEAT specifies, check that each record links to the previous one, and report "
        "breaks, altered records, bad NIFs, dates and amounts: use them to audit what any invoicing program "
        "exported. `verifactu_hash` computes one fingerprint. `generate_verifactu_qr` returns the AEAT tax QR URL "
        "and image (level M) with the printing rules. `build_facturae` turns plain invoice data into a Facturae "
        "3.2.2 XML validated against the official XSD (unsigned: sign it with the issuer's certificate before "
        "sending to FACe). `validate_facturae` checks a Facturae file: XSD, arithmetic, NIFs, DIR3 codes, "
        "signature presence. Free: `verifactu_calendar` (legal deadlines in force), `check_qr_url`, "
        "`validate_nif`, `pricing`, `credits_balance`. Amounts use a decimal point. This server is not an "
        "invoicing system and sends nothing to the AEAT. Unreadable input is never charged; failures are refunded."
    ),
)


UI = apps.ui_meta("verifactu")
apps.register(mcp, slug="verifactu", name="VeriFactu", icon="shield", version=__version__, lang="es",
              buy_url=lambda: billing.checkout_url,
              labels={"verifactu_hash": "Huella VERI*FACTU", "validate_invoice_chain": "Cadena de facturas", "validate_verifactu_xml": "XML VERI*FACTU", "generate_verifactu_qr": "QR tributario", "build_facturae": "Factura Facturae", "validate_facturae": "Validación Facturae", "credits_balance": "Saldo de créditos"})

@contextmanager
def _user_errors() -> Iterator[None]:
    try:
        yield
    except (huella.HuellaError, facturae.FacturaeError, ValueError) as exc:
        raise ToolError(str(exc)) from exc


def _headers(ctx: Context) -> Any:
    try:
        return ctx.headers
    except ValueError:
        return None


def _note(receipt: dict, credits: int) -> dict[str, Any]:
    return {"credits_charged": credits, "credits_remaining": receipt.get("remaining_balance")}


def _bytes(content: str, content_base64: str) -> bytes:
    if content_base64:
        try:
            raw = base64.b64decode(content_base64, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise huella.HuellaError("content_base64 no es base64 válido") from exc
    elif content:
        raw = content.encode("utf-8")
    else:
        raise huella.HuellaError("pasa el fichero en content o content_base64")
    if len(raw) > MAX_BYTES:
        raise huella.HuellaError(f"fichero demasiado grande (máximo {MAX_BYTES} bytes)")
    return raw


async def _charged(ctx: Context, tool: str) -> dict[str, Any]:
    async with billing.charge(PRICES[tool], tool, _headers(ctx)) as receipt:
        pass
    return _note(receipt, PRICES[tool])


@mcp.tool(annotations=LOCAL, meta=UI)
async def verifactu_hash(ctx: Context, record: dict[str, Any], previous_huella: str = "") -> dict[str, Any]:
    """Compute the VERI*FACTU fingerprint (huella, SHA-256) of one invoicing record.

    record (AEAT field names; snake_case aliases accepted):
      alta: IDEmisorFactura, NumSerieFactura, FechaExpedicionFactura (DD-MM-AAAA), TipoFactura (F1, F2, F3,
        R1-R5), CuotaTotal, ImporteTotal (as written in the XML, decimal point), FechaHoraHusoGenRegistro
        (AAAA-MM-DDThh:mm:ss+hh:mm).
      anulación (TipoRegistro="anulacion"): IDEmisorFacturaAnulada, NumSerieFacturaAnulada,
        FechaExpedicionFacturaAnulada, FechaHoraHusoGenRegistro.
    previous_huella: fingerprint of the previous record of the chain; empty for the first record.
    Returns the fingerprint, the exact string hashed and any field problems (an error means the AEAT would
    reject the record).
    """
    with _user_errors():
        rec = huella.normalize_record(record)
        issues = huella.field_issues(rec)
        out = huella.compute(rec, previous_huella.strip().upper())
    billing.client(_headers(ctx)).check()
    return {**out, "tipo": rec["TipoRegistro"], "valid": not any(i["level"] == "error" for i in issues),
            "issues": issues, "billing": await _charged(ctx, "verifactu_hash")}


@mcp.tool(annotations=LOCAL, meta=UI)
async def validate_invoice_chain(ctx: Context, records: list[dict[str, Any]],
                                 continues_from: str = "") -> dict[str, Any]:
    """Audit a VERI*FACTU chain of records (in generation order) given as JSON objects.

    Each record has the fields of `verifactu_hash` plus `Huella` (stored fingerprint) and optionally
    `HuellaAnterior` or `RegistroAnterior` {IDEmisorFactura, NumSerieFactura, FechaExpedicionFactura, Huella} and
    `PrimerRegistro` ("S"). For every record the fingerprint is recomputed and compared, and the link to the
    previous record checked. Reports chain breaks, altered records, PrimerRegistro misuse, duplicate invoices,
    cancellations of unknown invoices, timestamps going backwards, invalid NIF/dates/amounts/TipoFactura.
    continues_from: last fingerprint of the previous batch, to audit a chain in pieces. Up to 20,000 records.
    `last_huella` is what the next record must link to.
    """
    with _user_errors():
        if not isinstance(records, list) or not records:
            raise huella.HuellaError("records: lista de registros obligatoria")
        billing.client(_headers(ctx)).check()
        report = await asyncio.to_thread(huella.validate_chain, records, continues_from)
    report["results"] = report["results"][:PREVIEW]
    return {**report, "billing": await _charged(ctx, "validate_invoice_chain")}


def _xsd_check(root: Any) -> tuple[bool | None, list[str]]:
    lr = "https://www2.agenciatributaria.gob.es/static_files/common/internet/dep/aplicaciones/es/aeat/tike/cont/ws/" \
         "SuministroLR.xsd"
    target = root if etree.QName(root).localname == "RegFactuSistemaFacturacion" else \
        next(iter(root.iter(f"{{{lr}}}RegFactuSistemaFacturacion")), None)
    if target is None:
        return None, []
    sch = _lr_schema()
    ok = sch.validate(etree.ElementTree(etree.fromstring(etree.tostring(target))))
    return ok, [f"línea {e.line}: {e.message}" for e in list(sch.error_log)[:50]]


@lru_cache(maxsize=1)
def _lr_schema() -> etree.XMLSchema:
    with resources.as_file(resources.files("verifactu_mcp") / "schemas" / "SuministroLR.xsd") as p:
        return etree.XMLSchema(etree.parse(str(p)))


@mcp.tool(annotations=LOCAL, meta=UI)
async def validate_verifactu_xml(ctx: Context, content: str = "", content_base64: str = "",
                                 continues_from: str = "") -> dict[str, Any]:
    """Audit VERI*FACTU XML exported by an invoicing program (RegFactuSistemaFacturacion, a SOAP envelope, or any
    XML holding RegistroAlta / RegistroAnulacion elements, in document order).

    Validates RegFactuSistemaFacturacion against the official AEAT schema (SuministroLR.xsd) when present, then
    runs every chain and fingerprint check of `validate_invoice_chain`. XML that holds no records is not charged.
    """
    with _user_errors():
        raw = _bytes(content, content_base64)
        root, recs = await asyncio.to_thread(huella.parse_xml, raw)
        if not recs:
            raise huella.HuellaError("el XML no contiene RegistroAlta ni RegistroAnulacion")
        billing.client(_headers(ctx)).check()
        xsd_ok, xsd_errors = await asyncio.to_thread(_xsd_check, root)
        report = await asyncio.to_thread(huella.validate_chain, recs, continues_from)
    if xsd_ok is False:
        report["valid"] = False
    report["results"] = report["results"][:PREVIEW]
    return {**report, "schema_valid": xsd_ok, "schema_errors": xsd_errors,
            "billing": await _charged(ctx, "validate_verifactu_xml")}


@mcp.tool(annotations=LOCAL, meta=UI)
async def generate_verifactu_qr(ctx: Context, nif: str, numserie: str, fecha: str, importe: str,
                                verifactu: bool = True, environment: str = "produccion", size_px: int = 300,
                                image_format: str = "png") -> dict[str, Any]:
    """Generate the AEAT tax QR ("QR tributario") of an invoice: URL and image.

    Args:
        nif: Issuer NIF. numserie: Invoice series + number exactly as registered.
        fecha: Issue date (DD-MM-AAAA; AAAA-MM-DD and DD/MM/AAAA are converted).
        importe: ImporteTotal with decimal point, e.g. "241.40".
        verifactu: true for VERI*FACTU systems (ValidarQR), false for non-verifiable systems (ValidarQRNoVerifactu).
        environment: produccion (default) or pruebas (AEAT test portal).
        size_px: Image width in pixels (100-2000). image_format: png (default) or svg.
    The image uses error correction level M. `print_rules` says how it must appear on the invoice.
    """
    with _user_errors():
        url = qr.build_url(nif, numserie, fecha, importe, verifactu, environment)
        if image_format not in ("png", "svg"):
            raise huella.HuellaError("image_format debe ser png o svg")
        img = await asyncio.to_thread(qr.render, url, size_px, image_format)
    billing.client(_headers(ctx)).check()
    rules = dict(qr.PRINT)
    if not verifactu:
        rules.pop("leyenda_verifactu")
    return {"url": url, **img, "print_rules": rules, "billing": await _charged(ctx, "generate_verifactu_qr")}


@mcp.tool(annotations=LOCAL, meta=UI)
async def build_facturae(ctx: Context, invoice: dict[str, Any]) -> dict[str, Any]:
    """Build a Facturae 3.2.2 invoice (XML) from plain data, with totals computed and the official XSD checked.

    invoice:
      number (max 20), series, issue_date, operation_date?, currency (EUR), description?, notes?,
      legal_literals? [text], receiver_contract_reference?,
      seller / buyer: nif, name (company name, or first name with first_surname/second_surname for a person),
        address, post_code, town, province, country (ISO alfa-3, default ESP), residence? (R/U/E), email?, phone?;
        buyer.face: {oficina_contable, organo_gestor, unidad_tramitadora, organo_proponente?} DIR3 codes for
        public administration invoices (FACe), or administrative_centres: [{code, role 01-04, name?}],
      lines: [{description, quantity, unit_price, tax_rate (21, 10, 4, 0...), discount_pct?, surcharge_rate?
        (recargo de equivalencia 5.2, 1.4, 0.5), tax_type? (01 IVA, 03 IGIC, 02 IPSI), unit? (01 unidades),
        receiver_reference?, file_reference?, article_code?}],
      withholding?: {rate} (IRPF retention, e.g. 15 or 7), payment?: {due_date, method (transferencia,
        domiciliacion, efectivo, tarjeta...), iban, bic?}.
    Amounts accept "1.234,56" or "1234.56". Returns `xml` (UTF-8, unsigned), `xml_base64`, `totals` and the
    validation report. Invalid data is refused before charging. Sign the XML (XAdES) before sending to FACe.
    """
    with _user_errors():
        if not isinstance(invoice, dict):
            raise facturae.FacturaeError("invoice: objeto obligatorio")
        out = await asyncio.to_thread(facturae.build, invoice)
        if not out["valid"]:
            first = next(i for i in out["issues"] if i["level"] == "error")
            raise facturae.FacturaeError(f"la factura generada no supera la validación: {first['message']}")
    billing.client(_headers(ctx)).check()
    name = f"{(invoice.get('series') or '')}{invoice.get('number')}".replace("/", "-")
    return {**out, "filename": f"facturae_{name}.xml",
            "xml_base64": base64.b64encode(out["xml"].encode("utf-8")).decode(),
            "billing": await _charged(ctx, "build_facturae")}


@mcp.tool(annotations=LOCAL, meta=UI)
async def validate_facturae(ctx: Context, content: str = "", content_base64: str = "") -> dict[str, Any]:
    """Validate a Facturae 3.2.2 file (.xml or signed .xsig) before sending it to FACe or a client.

    Checks the official XSD, the arithmetic FACe checks (line amounts, tax quotas, header vs line bases,
    totals, batch totals, installments), NIFs of resident parties, the three DIR3 codes for public
    administrations, and whether the file is signed. Files that are not Facturae are not charged.
    """
    with _user_errors():
        raw = _bytes(content, content_base64)
        report = await asyncio.to_thread(facturae.validate, raw)
    billing.client(_headers(ctx)).check()
    return {**report, "billing": await _charged(ctx, "validate_facturae")}


@mcp.tool(annotations=LOCAL)
def check_qr_url(url: str) -> dict[str, Any]:
    """Check a tax QR URL read from an invoice: official AEAT host, path, parameters and formats. Free."""
    return qr.parse_url(url)


@mcp.tool(annotations=LOCAL)
def validate_nif(nif: str) -> dict[str, Any]:
    """Check a Spanish NIF / NIE / CIF control character. Free."""
    return ids.validate_nif(nif)


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=False))
def verifactu_calendar() -> dict[str, Any]:
    """Legal deadlines for VERI*FACTU and mandatory B2B e-invoicing in Spain, with sources and what is still
    pending publication. Free."""
    return calendar.CALENDAR


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=False))
def pricing() -> dict[str, Any]:
    """Credit price of each tool and where to buy credits. Free."""
    free = {k: 0 for k in ("check_qr_url", "validate_nif", "verifactu_calendar")}
    return {"credits_per_call": {**PRICES, **free},
            "refunds": "Unreadable or invalid input is never charged; a tool that fails after charging is refunded.",
            "buy_credits": billing.checkout_url or "Set KEYFLINT_CHECKOUT_URL"}


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=False), meta=UI)
async def credits_balance(ctx: Context) -> dict[str, Any]:
    """Remaining credits on your KeyFlint license. Free."""
    return await billing.balance(_headers(ctx))


def main() -> None:
    p = argparse.ArgumentParser(prog="verifactu-mcp", description=__doc__)
    p.add_argument("--http", action="store_true", help="Serve Streamable HTTP (hosted, license per request).")
    p.add_argument("--host", default=os.environ.get("HOST", "0.0.0.0"))
    p.add_argument("--port", type=int, default=int(os.environ.get("PORT", 8080)))
    args = p.parse_args()
    if args.http:
        billing.hosted = True
        mcp.run("streamable-http", host=args.host, port=args.port, stateless_http=True, json_response=True)
    else:
        mcp.run("stdio")


if __name__ == "__main__":
    main()
