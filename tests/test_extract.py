from pathlib import Path

from delivery_sync.extract import extract
from delivery_sync.mailparse import parse_message
from delivery_sync.prefilter import classify
from delivery_sync.rules import load_rules

FIXTURES = Path(__file__).parent / "fixtures"


def _parsed(name: str):
    return parse_message((FIXTURES / name).read_bytes())


def test_headers_and_text_parsed():
    mail = _parsed("amazon_shipped.eml")
    assert mail.gm_msgid == "1001"
    assert mail.from_addr == "shipment-tracking@amazon.com"
    assert mail.from_name == "Amazon"
    assert "1Z999AA10123456784" in mail.text
    assert mail.subject == "Your package is on the way"


def test_html_only_body_extracts_text():
    raw = (
        b"From: shop@example.com\r\n"
        b"Subject: delivery\r\n"
        b"Date: Fri, 20 Nov 2026 09:00:00 +0200\r\n"
        b"Message-ID: <html-1@example.com>\r\n"
        b"Content-Type: text/html; charset=utf-8\r\n\r\n"
        b"<html><body><p>Tracking: TBA123456789015</p>"
        b"<p>Delivery address: Herzl 12, Tel Aviv</p></body></html>"
    )
    mail = parse_message(raw)
    assert "TBA123456789015" in mail.text
    assert "Herzl 12" in mail.text


def test_hebrew_body_preserves_digits():
    mail = _parsed("hfd_pickup.eml")
    assert "RR123456789IL" in mail.text
    assert "הרצל 12" in mail.text


def test_quoted_reply_stripped():
    raw = (
        b"From: a@b.c\r\nSubject: s\r\nDate: Fri, 20 Nov 2026 09:00:00 +0200\r\n"
        b"Message-ID: <q@b.c>\r\nContent-Type: text/plain; charset=utf-8\r\n\r\n"
        b"Tracking: TBA123456789016\r\n\r\n"
        b"On Fri, 20 Nov 2026 at 08:00, someone wrote:\r\n"
        b"> old tracking TBA999999999999\r\n"
    )
    mail = parse_message(raw)
    assert "TBA123456789016" in mail.text
    assert "TBA999999999999" not in mail.text


def test_promo_with_unsubscribe_is_promo():
    rules = load_rules(None, None)
    mail = _parsed("promo.eml")
    verdict = classify(mail, rules)
    assert verdict.kind == "promo"


def test_delivery_mail_with_tracking_is_delivery():
    rules = load_rules(None, None)
    verdict = classify(_parsed("amazon_shipped.eml"), rules)
    assert verdict.kind == "delivery"


def test_multi_tracking_mail_yields_three_candidates():
    rules = load_rules(None, None)
    candidates = extract(_parsed("amazon_multi.eml"), rules, min_confidence=0.7)
    assert len(candidates) == 3
    keys = {c.shipment.shipment_key for c in candidates}
    assert len(keys) == 3


def test_arrival_mail_without_tracking_is_review():
    rules = load_rules(None, None)
    candidates = extract(_parsed("cheetah_arrived_no_tracking.eml"), rules, min_confidence=0.7)
    assert len(candidates) == 1
    shipment = candidates[0].shipment
    assert shipment.needs_review is True
    assert "no_tracking_number" in shipment.review_reasons


def test_pickup_mail_is_arrival_with_location_kind():
    rules = load_rules(None, None)
    candidates = extract(_parsed("hfd_pickup.eml"), rules, min_confidence=0.7)
    shipment = candidates[0].shipment
    assert str(shipment.status) == "ready_for_pickup"
    assert str(shipment.location_kind) == "pickup_point"
    assert shipment.arrival_date is not None
    assert shipment.tracking_number == "RR123456789IL"


def test_in_transit_mail_has_no_arrival_date():
    rules = load_rules(None, None)
    candidates = extract(_parsed("amazon_shipped.eml"), rules, min_confidence=0.7)
    shipment = candidates[0].shipment
    assert str(shipment.status) == "in_transit"
    assert shipment.arrival_date is None
