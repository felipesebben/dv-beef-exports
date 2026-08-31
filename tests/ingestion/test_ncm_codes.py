"""Tests fr the beef NCM coide reference table."""

from dv_beef_exports.ingestion.ncm_codes import BEEF_NCM_CODES, all_codes


def test_codes_are_eight_digit_strings() -> None:
    for ncm in BEEF_NCM_CODES:
        assert len(ncm.code) == 8
        assert ncm.code.isdigit()


def test_no_duplicate_codes() -> None:
    codes = [ncm.code for ncm in BEEF_NCM_CODES]
    assert len(codes) == len(set(codes))


def test_all_codes_matches_table_length() -> None:
    assert len(all_codes()) == len(BEEF_NCM_CODES)


def test_all_codes_returns_strings() -> None:
    assert all(isinstance(code, str) for code in all_codes())
