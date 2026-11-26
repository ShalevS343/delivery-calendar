from delivery_calendar.links import classify_action, extract_urls, unwrap


def test_confirm_delivery_link_classified():
    text = "Confirm your delivery: https://courier.example.com/track/confirm?id=42"
    action = classify_action(text, extract_urls(text))
    assert action is not None
    assert action.kind == "confirm_delivery"
    assert action.url.endswith("id=42")


def test_unsubscribe_dropped():
    text = (
        "Unsubscribe: https://shop.example.com/unsubscribe?u=1\n"
        "Confirm receipt: https://shop.example.com/approve/9"
    )
    action = classify_action(text, extract_urls(text))
    assert action is not None
    assert "approve" in action.url


def test_social_and_pixels_dropped():
    text = (
        "https://facebook.com/shop "
        "https://cdn.example.com/pixel.gif "
        "https://example.com/empty"
    )
    assert extract_urls(text) == ["https://example.com/empty"]


def test_redirector_unwrapped():
    url = (
        "https://track.example.com/click?url="
        "https%3A%2F%2Fcourier.example.com%2Fconfirm%3Fid%3D7"
    )
    assert unwrap(url) == "https://courier.example.com/confirm?id=7"


def test_reschedule_link_classified():
    text = "Need another day? Reschedule here: https://courier.example.com/reschedule/9"
    action = classify_action(text, extract_urls(text))
    assert action is not None
    assert action.kind == "reschedule"


def test_no_action_link_returns_none():
    text = "Track your parcel at https://courier.example.com/track/9"
    assert classify_action(text, extract_urls(text)) is None
