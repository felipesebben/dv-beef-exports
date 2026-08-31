"""
Reference table of beef-related NCM codes this pipeline tracks.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class NcmCode:
    """One 8-digit NCM product code and its metadata."""

    code: str
    description_pt: str
    description_en: str
    category: str


BEEF_NCM_CODES: tuple[NcmCode, ...] = (
    NcmCode(
        code="02021000",
        description_pt="Carcaças e meias-carcaças de bovino, congeladas",
        description_en="Carcasses/half-carcasses, frozen",
        category="frozen",
    ),
    NcmCode(
        code="02022010",
        description_pt="Quartos dianteiros não desossados de bovino, congelados",
        description_en="Forequarters, bone-in, frozen",
        category="frozen",
    ),
    NcmCode(
        code="02022020",
        description_pt="Quartos traseiros não desossados de bovino, congelados",
        description_en="Hindquarters, bone-in, frozen",
        category="frozen",
    ),
    NcmCode(
        code="02022090",
        description_pt="Outras peças não desossadas de bovino, congeladas",
        description_en="Other bone-in cuts, frozen",
        category="frozen",
    ),
    NcmCode(
        code="02023000",
        description_pt="Carnes desossadas de bovino, congeladas",
        description_en="Boneless beef, frozen",
        category="frozen",
    ),
    NcmCode(
        code="02062100",
        description_pt="Línguas de bovino, congeladas",
        description_en="Tongues, frozen",
        category="offal",
    ),
    NcmCode(
        code="02062200",
        description_pt="Fígados de bovino, congelados",
        description_en="Livers, frozen",
        category="offal",
    ),
    NcmCode(
        code="02062910",
        description_pt="Rabos de bovino, congelados",
        description_en="Tails, frozen",
        category="offal",
    ),
    NcmCode(
        code="02062990",
        description_pt="Outras miudezas comestíveis de bovino, congeladas",
        description_en="Other edible offal, frozen",
        category="offal",
    ),
    NcmCode(
        code="02102000",
        description_pt="Carnes de bovinos, salgadas, em salmoura, secas ou defumadas",
        description_en="Salted, brined, dried, or smoked beef",
        category="salted_dried",
    ),
    NcmCode(
        code="16025000",
        description_pt="Preparações alimentícias e conservas, da espécie bovina",
        description_en="Prepared or preserved beef (incl. corned beef)",
        category="processed",
    ),
)


def all_codes() -> list[str]:
    """Return just the code strings, e.g., for passing to the API's NCM filter."""
    return [ncm.code for ncm in BEEF_NCM_CODES]
