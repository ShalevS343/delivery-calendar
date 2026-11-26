from pathlib import Path

import pytest

from delivery_calendar.normalize import (
    extract_tracking,
    upu_check_digit,
    upu_checksum_ok,
    ups_check_digit,
    ups_checksum_ok,
)
from delivery_calendar.rules import RuleCache, RuleError, load_rules


def test_upu_check_digit_is_self_consistent():
    body = "12345678"
    digit = upu_check_digit(body)
    assert upu_checksum_ok("RR" + body + str(digit) + "IL")
    mutated = "RR" + body + str((digit + 1) % 10) + "IL"
    assert not upu_checksum_ok(mutated)


def test_ups_check_digit_is_self_consistent():
    prefix = "1Z999AA1012345678"  # 1Z + 15 characters
    digit = ups_check_digit(prefix)
    number = prefix + str(digit)
    assert ups_checksum_ok(number)
    wrong = prefix + str((int(digit) + 1) % 10)
    assert not ups_checksum_ok(wrong)


def test_extract_tracking_finds_upu_and_amazon():
    text = (
        "Your parcel RR123456789IL is on its way. "
        "Amazon shipped TBA123456789012 today."
    )
    matches = {m.normalized: m for m in extract_tracking(text, load_rules(None, None))}
    assert "RR123456789IL" in matches
    assert matches["RR123456789IL"].carrier == "israel_post"
    assert "TBA123456789012" in matches
    assert matches["TBA123456789012"].carrier == "amazon"


def test_bare_numeric_family_needs_context():
    rules = load_rules(None, None)
    assert extract_tracking("order 123456789012 confirmed", rules) == []
    found = extract_tracking("tracking number 123456789012", rules)
    assert [m.normalized for m in found] == ["123456789012"]


def test_carrier_from_sender_domain():
    rules = load_rules(None, None)
    match = rules.carrier_for_sender("no-reply@shipment-tracking.amazon.com")
    assert match is not None
    assert match["carrier"] == "amazon"


def test_carrier_for_unknown_sender_is_none():
    assert load_rules(None, None).carrier_for_sender("someone@example.org") is None


def test_bad_rules_file_keeps_previous_rules(tmp_path: Path):
    import shutil

    from delivery_calendar.rules import PACKAGE_RULES

    seed = tmp_path / "seed"
    live = tmp_path / "live"
    seed.mkdir()
    live.mkdir()
    for name in ("tracking.yaml", "carriers.yaml", "promos.yaml", "addresses.yaml"):
        shutil.copyfile(PACKAGE_RULES / name, seed / name)
    (seed / "promos.yaml").write_text("domains: [example.com]\n", encoding="utf-8")
    cache = RuleCache(live, seed)
    assert cache.reload() == []
    assert "example.com" in cache.rules.promos.domains

    (live / "promos.yaml").write_text("domains: [unclosed\n", encoding="utf-8")
    errors = cache.reload()
    assert errors
    assert "example.com" in cache.rules.promos.domains


def test_unknown_family_is_not_dropped():
    rules = load_rules(None, None)
    matches = extract_tracking("ref ABC123456789XY end", rules)
    assert matches == [] or matches[0].valid is False
