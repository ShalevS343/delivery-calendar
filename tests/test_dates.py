from datetime import date

from delivery_calendar.dates import extract_dates


def _facts(text: str, mail_day: date = date(2026, 11, 20)):
    return extract_dates(text, mail_day, ("he", "en"))


def test_tomorrow_resolves_against_mail_date():
    facts = _facts("Your parcel is arriving tomorrow.")
    assert facts.eta_date == date(2026, 11, 21)


def test_window_parsed():
    facts = _facts("Delivery expected between 10:00 and 14:00.")
    assert (facts.window_start, facts.window_end) == ("10:00", "14:00")


def test_window_with_dash_parsed():
    facts = _facts("Delivery window 15:30-18:00.")
    assert (facts.window_start, facts.window_end) == ("15:30", "18:00")


def test_absurd_future_date_rejected():
    facts = _facts("Your parcel arrives on 2031-01-01.")
    assert facts.eta_date is None
    assert facts.rejected


def test_explicit_arrival_phrase_sets_arrival_date():
    facts = _facts("Your parcel is ready for pickup at the collection point.")
    assert facts.explicit_arrival is True
    assert facts.arrival_date == date(2026, 11, 20)


def test_delivered_phrase_sets_arrival():
    facts = _facts("Your package was delivered.")
    assert facts.explicit_arrival is True
    assert facts.arrival_date == date(2026, 11, 20)


def test_pickup_deadline_from_hold_period():
    facts = _facts("Your parcel is ready for pickup. Please collect within 7 days.")
    assert facts.arrival_date == date(2026, 11, 20)
    assert facts.pickup_deadline == date(2026, 11, 27)


def test_hebrew_arrival_phrase():
    facts = _facts("המשלוח שלך מוכן לאיסוף בנקודת האיסוף")
    assert facts.explicit_arrival is True


def test_no_dates_gives_empty_facts():
    facts = _facts("Thanks for shopping with us.")
    assert facts.eta_date is None
    assert facts.arrival_date is None
    assert facts.explicit_arrival is False
