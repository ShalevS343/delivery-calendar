from delivery_calendar.config import Settings


def test_settings_defaults():
    s = Settings.from_env({})
    assert s.poll_interval_minutes == 60
    assert s.geocoder == "none"
    assert s.model_enabled is False
    assert s.dry_run is True
    assert s.calendar_auth == "service_account"
    assert s.parser_languages == ("he", "en")
    assert s.default_country == "IL"


def test_settings_parses_env_values():
    s = Settings.from_env(
        {
            "MAIL_USER": "me@example.com",
            "MAIL_APP_PASSWORD": "abcd efgh ijkl mnop",
            "CALENDAR_ID": "abc@group.calendar.google.com",
            "POLL_INTERVAL_MINUTES": "15",
            "DRY_RUN": "0",
            "PARSER_LANGUAGES": "he,en,ru",
        }
    )
    assert s.mail_user == "me@example.com"
    assert s.poll_interval_minutes == 15
    assert s.dry_run is False
    assert s.parser_languages == ("he", "en", "ru")


def test_settings_rejects_bad_value():
    import pytest

    with pytest.raises(ValueError, match="POLL_INTERVAL_MINUTES"):
        Settings.from_env({"POLL_INTERVAL_MINUTES": "soon"})


def test_validate_reports_missing_calendar_id_for_service_account():
    s = Settings.from_env({"DRY_RUN": "0", "MAIL_APP_PASSWORD": "x"})
    problems = s.validate()
    assert any("CALENDAR_ID" in p for p in problems)


def test_validate_clean_in_dry_run():
    s = Settings.from_env({})
    assert s.validate() == []


def test_app_password_and_user_are_whitespace_normalised():
    s = Settings.from_env(
        {
            "MAIL_USER": " me@gmail.com ",
            "MAIL_APP_PASSWORD": "abcd efgh ijkl mnop",
        }
    )
    assert s.mail_user == "me@gmail.com"
    assert s.mail_app_password == "abcdefghijklmnop"
