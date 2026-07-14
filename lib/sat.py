"""
lib/sat.py — Catálogos del SAT (CFDI 4.0).

Solo los que usa una empresa de logística. La lista completa del SAT tiene
más claves, pero cargarlas todas solo confunde al asesor en el formulario.

Fuente: catálogos c_RegimenFiscal y c_UsoCFDI del SAT.
Si el SAT los actualiza, se cambian aquí y en ningún otro lado.
"""

# --- Régimen fiscal ---
# El asesor debe copiarlo TAL CUAL viene en la Constancia de Situación Fiscal.
# No lo adivines: si el régimen no coincide con el que tiene el cliente
# registrado ante el SAT, el CFDI se rechaza al timbrarlo.

REGIMEN_MORAL = {
    "601": "601 · General de Ley Personas Morales",
    "603": "603 · Personas Morales con Fines no Lucrativos",
    "620": "620 · Sociedades Cooperativas de Producción",
    "622": "622 · Actividades Agrícolas, Ganaderas, Silvícolas y Pesqueras",
    "623": "623 · Opcional para Grupos de Sociedades",
    "624": "624 · Coordinados",
    "626": "626 · Régimen Simplificado de Confianza (RESICO)",
}

REGIMEN_FISICA = {
    "605": "605 · Sueldos y Salarios",
    "606": "606 · Arrendamiento",
    "607": "607 · Enajenación o Adquisición de Bienes",
    "608": "608 · Demás ingresos",
    "611": "611 · Ingresos por Dividendos",
    "612": "612 · Actividades Empresariales y Profesionales",
    "614": "614 · Ingresos por intereses",
    "615": "615 · Régimen de los ingresos por obtención de premios",
    "616": "616 · Sin obligaciones fiscales",
    "621": "621 · Incorporación Fiscal",
    "625": "625 · Actividades Empresariales vía plataformas tecnológicas",
    "626": "626 · Régimen Simplificado de Confianza (RESICO)",
}

# --- Uso de CFDI ---
# Para servicios de logística, casi siempre es G03 (Gastos en general).
USO_CFDI = {
    "G01": "G01 · Adquisición de mercancías",
    "G02": "G02 · Devoluciones, descuentos o bonificaciones",
    "G03": "G03 · Gastos en general",
    "I01": "I01 · Construcciones",
    "I04": "I04 · Equipo de cómputo y accesorios",
    "I08": "I08 · Otra maquinaria y equipo",
    "S01": "S01 · Sin efectos fiscales",
    "CP01": "CP01 · Pagos",
}


def es_persona_moral(rfc: str) -> bool:
    """
    RFC de 12 caracteres = persona moral (empresa).
    RFC de 13 caracteres = persona física.

    Esto determina qué regímenes se le ofrecen al asesor: no tiene sentido
    mostrarle 'Sueldos y Salarios' a una S.A. de C.V.
    """
    return len(rfc.strip()) == 12


def regimenes_para(rfc: str) -> dict:
    """Devuelve solo los regímenes válidos para ese tipo de RFC."""
    if not rfc or len(rfc.strip()) < 12:
        # RFC incompleto: muestra todos, no adivines.
        return {**REGIMEN_MORAL, **REGIMEN_FISICA}
    return REGIMEN_MORAL if es_persona_moral(rfc) else REGIMEN_FISICA
