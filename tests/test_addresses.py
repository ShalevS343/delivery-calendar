from delivery_calendar.addresses import extract_locations
from delivery_calendar.rules import load_rules


def test_pickup_label_sets_kind_pickup_point():
    text = (
        "Your parcel is ready for pickup.\n"
        "Pickup point: HaHistadrut 45, Haifa\n"
    )
    locations = extract_locations(text, load_rules(None, None))
    assert locations
    best = locations[0]
    assert best.kind == "pickup_point"
    assert "HaHistadrut 45" in best.text


def test_destination_wins_over_fallback():
    text = (
        "Delivery address: Herzl 12, Tel Aviv\n"
        "If you are not home, collect at: Dizengoff 99, Tel Aviv\n"
    )
    locations = extract_locations(text, load_rules(None, None))
    kinds = [(loc.kind, loc.is_fallback) for loc in locations]
    assert ("destination", False) in kinds
    assert ("pickup_point", True) in kinds or ("destination", True) in kinds
    assert locations[0].is_fallback is False


def test_label_without_street_number_is_not_a_location():
    text = "Delivery address: somewhere in Tel Aviv\n"
    assert extract_locations(text, load_rules(None, None)) == []


def test_unlabelled_address_with_number_is_found():
    text = "We will deliver to Rothschild 22, Tel Aviv on Sunday.\n"
    locations = extract_locations(text, load_rules(None, None))
    assert any("Rothschild 22" in loc.text for loc in locations)


def test_hebrew_labels():
    text = "נקודת איסוף: הרצל 12, תל אביב\n"
    locations = extract_locations(text, load_rules(None, None))
    assert locations and locations[0].kind == "pickup_point"
