"""AEAT tax QR ("QR tributario") for VERI*FACTU and non-VERI*FACTU invoicing systems.

Source: AEAT, "Características del QR y especificaciones del servicio de cotejo o remisión de información por
parte del receptor de la factura" (Orden HAC/1177/2024, art. 20 and 21): URL with parameters nif, numserie,
fecha (DD-MM-AAAA) and importe (decimal point), UTF-8 URL-encoded; ISO/IEC 18004 with error correction level M,
printed 30x30 to 40x40 mm, under the label "QR tributario:"; VERI*FACTU invoices also carry "VERI*FACTU" or
"Factura verificable en la sede electrónica de la AEAT".
"""

from __future__ import annotations

import base64
import io
from typing import Any
from urllib.parse import parse_qsl, quote, urlencode, urlsplit

import segno

from . import ids
from .huella import AMOUNT_RE, DATE_RE, HuellaError, to_aeat_date

HOSTS = {"produccion": "https://www2.agenciatributaria.gob.es", "pruebas": "https://prewww2.aeat.es"}
PATHS = {True: "/wlpl/TIKE-CONT/ValidarQR", False: "/wlpl/TIKE-CONT/ValidarQRNoVerifactu"}
PRINT = {
    "tamano_mm": "entre 30x30 y 40x40 mm",
    "correccion_errores": "M (ISO/IEC 18004)",
    "etiqueta_superior": "QR tributario:",
    "leyenda_verifactu": "VERI*FACTU (o «Factura verificable en la sede electrónica de la AEAT»)",
    "posicion": "al inicio de la factura, antes del contenido",
}


def _amount(value: Any) -> str:
    v = str(value).strip().replace(",", ".") if value is not None else ""
    if v.count(".") > 1 or not AMOUNT_RE.fullmatch(v):
        raise HuellaError(f"importe no válido: {value!r} (punto decimal, máximo 2 decimales)")
    return v


def build_url(nif: str, numserie: str, fecha: str, importe: Any, verifactu: bool = True,
              environment: str = "produccion") -> str:
    nif = (nif or "").strip().upper()
    if len(nif) != 9 or not ids.validate_nif(nif)["valid"]:
        raise HuellaError(f"NIF del emisor no válido: {nif!r}")
    numserie = (numserie or "").strip()
    if not numserie or len(numserie) > 60:
        raise HuellaError("numserie obligatorio, máximo 60 caracteres")
    if environment not in HOSTS:
        raise HuellaError("environment debe ser 'produccion' o 'pruebas'")
    query = urlencode({"nif": nif, "numserie": numserie, "fecha": to_aeat_date(fecha), "importe": _amount(importe)},
                      quote_via=quote, safe="")
    return f"{HOSTS[environment]}{PATHS[verifactu]}?{query}"


def render(url: str, size_px: int = 300, fmt: str = "png") -> dict[str, Any]:
    if not 100 <= size_px <= 2000:
        raise HuellaError("size_px entre 100 y 2000")
    code = segno.make(url, error="m", boost_error=False, micro=False)
    modules = code.symbol_size(border=4)[0]
    scale = max(1, size_px // modules)
    buf = io.BytesIO()
    if fmt == "svg":
        code.save(buf, kind="svg", scale=scale, border=4, xmldecl=False)
        mime = "image/svg+xml"
    else:
        code.save(buf, kind="png", scale=scale, border=4)
        mime = "image/png"
    return {"mime_type": mime, "image_base64": base64.b64encode(buf.getvalue()).decode(),
            "qr_version": code.version, "error_correction": code.error, "pixels": modules * scale}


def parse_url(url: str) -> dict[str, Any]:
    parts = urlsplit((url or "").strip())
    issues: list[str] = []
    host = f"{parts.scheme}://{parts.netloc}"
    env = next((k for k, v in HOSTS.items() if v == host), None)
    if env is None:
        issues.append(f"host no oficial de la AEAT: {host}")
    verifactu = {v: k for k, v in PATHS.items()}.get(parts.path)
    if verifactu is None:
        issues.append(f"ruta no reconocida: {parts.path}")
    params = dict(parse_qsl(parts.query, keep_blank_values=True))
    for p in ("nif", "numserie", "fecha", "importe"):
        if not params.get(p):
            issues.append(f"falta el parámetro {p}")
    if "formato" in params:
        issues.append("el parámetro 'formato' no debe aparecer en el QR")
    extra = set(params) - {"nif", "numserie", "fecha", "importe", "formato"}
    if extra:
        issues.append(f"parámetros no previstos: {', '.join(sorted(extra))}")
    if params.get("nif") and not ids.validate_nif(params["nif"])["valid"]:
        issues.append("NIF con dígito de control incorrecto")
    if params.get("fecha") and not DATE_RE.fullmatch(params["fecha"]):
        issues.append("fecha debe ir en DD-MM-AAAA")
    if params.get("importe") and not AMOUNT_RE.fullmatch(params["importe"]):
        issues.append("importe con formato no válido (punto decimal, máximo 2 decimales)")
    return {"valid": not issues, "environment": env, "verifactu": verifactu, "params": params, "issues": issues}
