"""Legal calendar. Update when the BOE publishes changes; `as_of` says when it was last checked."""

CALENDAR = {
    "as_of": "2026-11-05",
    "verifactu": {
        "norma": "Ley 11/2021 (art. 29.2.j LGT), Real Decreto 1007/2023 (RRSIF), Orden HAC/1177/2024 "
                 "(especificaciones técnicas), plazos del Real Decreto-ley 15/2025 (BOE 03-12-2025)",
        "en_vigor": [
            {"obligados": "Contribuyentes del Impuesto sobre Sociedades", "fecha": "2027-01-01"},
            {"obligados": "Resto (autónomos IRPF, entidades en atribución de rentas, IRNR con establecimiento "
                          "permanente)", "fecha": "2027-07-01"},
        ],
        "pendiente": "Hacienda anunció el 05-10-2026 un nuevo aplazamiento a octubre de 2028 para todos los "
                     "obligados; a fecha as_of no está publicado en el BOE, así que rigen las fechas en_vigor.",
        "excluidos": "Quienes llevan los libros registro en la sede electrónica de la AEAT (SII) y los "
                     "territorios forales con su propio sistema (TicketBAI en País Vasco; Navarra).",
        "fabricantes_sif": "Los sistemas informáticos de facturación debían adaptarse desde el 29-07-2025 "
                           "(declaración responsable del productor).",
    },
    "factura_electronica_b2b": {
        "norma": "Ley 18/2022 (Crea y Crece), Real Decreto 238/2026 (BOE 31-03-2026), Orden HAC/1028/2026 "
                 "(BOE 05-10-2026, en vigor 06-10-2026)",
        "plazos": [
            {"obligados": "Volumen de operaciones > 8 M EUR", "fecha": "2027-10-06"},
            {"obligados": "Resto de empresarios y profesionales", "fecha": "2028-10-06"},
        ],
        "formatos": "Modelo semántico EN 16931 en sintaxis UBL, CII, EDIFACT o Facturae.",
        "estados": "Comunicación de aceptación/rechazo y pago efectivo; plazos escalonados hasta 2029.",
    },
    "facturae_b2g": "Facturae 3.2.x firmada (XAdES) obligatoria con las Administraciones Públicas vía FACe "
                    "(Ley 25/2013) desde 2015.",
    "fuentes": [
        "https://sede.agenciatributaria.gob.es/Sede/iva/sistemas-informaticos-facturacion-verifactu.html",
        "https://sede.agenciatributaria.gob.es/Sede/iva/novedades-iva/novedades-normativa-2026/"
        "orden-hac-1028-2026-2-electronica.html",
    ],
    "aviso": "Información orientativa; confirma siempre en el BOE antes de decidir.",
}
