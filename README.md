# verifactu-mcp

MCP server for Spanish invoicing compliance, built for AI agents working for gestorías, accountants and
invoicing-software teams. Offline and stateless: nothing is stored and nothing is sent to the AEAT.

- **Audit VERI\*FACTU chains**: recompute the SHA-256 fingerprint (*huella*) of every record (alta and anulación)
  exactly as the AEAT specifies, and check that each record links to the previous one. Works on JSON records or
  on the XML any invoicing program exports (validated against the official `SuministroLR.xsd`).
- **AEAT tax QR**: URL and PNG/SVG image (error correction M) for VERI\*FACTU and non-VERI\*FACTU systems,
  production or test portal, with the printing rules.
- **Facturae 3.2.2**: build an invoice from plain data (totals, VAT breakdown, IRPF withholding, recargo de
  equivalencia, DIR3 codes for FACe, IBAN) and validate any Facturae file: official XSD, the arithmetic FACe
  checks, NIFs, DIR3 roles and signature presence.

The fingerprint code is tested against the three reference vectors published by the AEAT.

## Tools

| Tool | Credits | What it does |
|---|---|---|
| `validate_invoice_chain` | 3 | Audit a chain of records (JSON, up to 20,000): fingerprints, links, PrimerRegistro, duplicates, cancellations of unknown invoices, timestamps, NIF/date/amount/TipoFactura formats. `continues_from` audits a chain in pieces. |
| `validate_verifactu_xml` | 3 | Same audit on exported XML (`RegFactuSistemaFacturacion`, SOAP envelope or any XML with `RegistroAlta`/`RegistroAnulacion`), plus XSD validation. |
| `verifactu_hash` | 1 | Fingerprint of one record, with the exact string hashed. |
| `generate_verifactu_qr` | 1 | Tax QR URL + image + printing rules. |
| `build_facturae` | 3 | Facturae 3.2.2 XML (unsigned) from plain invoice data, XSD-validated. |
| `validate_facturae` | 2 | XSD + arithmetic + NIF + DIR3 + signature check of a Facturae file. |
| `verifactu_calendar` | free | Deadlines in force and what is pending in the BOE, with sources. |
| `check_qr_url`, `validate_nif`, `pricing`, `credits_balance` | free | Helpers. |

Unreadable or invalid input is never charged; a tool that fails after charging is refunded.

## Install

Claude Desktop / Claude Code / Cursor (`mcpServers` entry):

```json
{
  "verifactu": {
    "command": "uvx",
    "args": ["--from", "git+https://github.com/ArceeDev/verifactu-mcp", "verifactu-mcp"],
    "env": { "KEYFLINT_LICENSE": "LIC-...", "KEYFLINT_URL": "https://licenses.keyflint.dev" }
  }
}
```

Hosted (one server, many buyers): `verifactu-mcp --http` or the Dockerfile; each request carries
`Authorization: Bearer LIC-...`.

Environment: `KEYFLINT_LICENSE`, `KEYFLINT_URL`, `KEYFLINT_PUBLIC_KEYS`, `KEYFLINT_CHECKOUT_URL`,
`VERIFACTU_PRICES` (e.g. `validate_invoice_chain=5,build_facturae=4`), `VERIFACTU_MAX_BYTES` (default 10 MB).

## Legal status (checked 2026-11-05)

- VERI\*FACTU (RD 1007/2023, Orden HAC/1177/2024; dates from RD-ley 15/2025): **1 Jan 2027** for corporate
  income tax payers, **1 Jul 2027** for everyone else. Hacienda announced on 5 Oct 2026 a further delay to
  October 2028, not yet published in the BOE. Taxpayers on SII and the foral territories are out of scope.
- Mandatory B2B e-invoicing (RD 238/2026, Orden HAC/1028/2026): 6 Oct 2027 for turnover above 8 M EUR, 6 Oct
  2028 for the rest; EN 16931 in UBL, CII, EDIFACT or Facturae.
- Facturae with XAdES signature is mandatory for invoices to public administrations through FACe.

`verifactu_calendar` returns this with sources. Always confirm in the BOE.

## Scope and limits

- Not an invoicing system (SIF): it keeps no records, sends nothing to the AEAT and does not replace the
  producer's *declaración responsable*. Use it to check what a SIF produced, or to prepare data.
- `build_facturae` produces unsigned XML. Sign it (XAdES-EPES) with the issuer's certificate before sending to
  FACe or a B2B platform. Corrective invoices (`InvoiceClass` OR) and batches are validated but not built yet.
- If a stored fingerprint only matches after removing trailing zeros from the amounts, it is reported as a
  warning (`correcta_sin_ceros`): the record was generated with amounts written differently from the XML.

## Sources

- AEAT, *Detalle de las especificaciones técnicas para generación de la huella o hash de los registros de
  facturación* (reference vectors in `tests/test_core.py`).
- AEAT, *Características del QR y especificaciones del servicio de cotejo*.
- AEAT web service schemas `SuministroLR.xsd` / `SuministroInformacion.xsd`, and `Facturaev3_2_2.xsd`
  (facturae.gob.es), bundled in `src/verifactu_mcp/schemas/` with the W3C xmldsig schema.

## Development

```bash
pip install -e '.[test]'
pytest -q
```

MIT licensed. The bundled XSD files belong to their publishers (AEAT, Ministerio de Hacienda, W3C).
