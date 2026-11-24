"""Rules are data.

Seed YAML ships in the image; live copies live on the data volume so they can be
edited without a rebuild. A broken edit must never take the service down: the
last good rule set stays active.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from pathlib import Path

import yaml

PACKAGE_RULES = Path(__file__).resolve().parent.parent / "rules"

RULE_FILES = ("tracking.yaml", "carriers.yaml", "promos.yaml", "addresses.yaml")


class RuleError(ValueError):
    """A rule file is missing or malformed."""


@dataclass(frozen=True)
class TrackingFamily:
    id: str
    pattern: str
    carrier: str | None
    checksum: str | None
    confidence: float
    needs_context: bool


@dataclass(frozen=True)
class TrackingRules:
    families: tuple[TrackingFamily, ...] = ()
    context_words: tuple[str, ...] = ()


@dataclass(frozen=True)
class SenderRule:
    domain: str
    carrier: str
    display: str


@dataclass(frozen=True)
class CarrierRules:
    senders: tuple[SenderRule, ...] = ()
    display: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class PromoRules:
    domains: tuple[str, ...] = ()
    keywords: tuple[str, ...] = ()


@dataclass(frozen=True)
class AddressRules:
    labels: dict[str, tuple[str, ...]] = field(default_factory=dict)
    fallback_labels: tuple[str, ...] = ()
    street_then_number: str = ""
    number_then_street: str = ""
    postal_code: str = ""


@dataclass(frozen=True)
class Rules:
    tracking: TrackingRules = TrackingRules()
    carriers: CarrierRules = CarrierRules()
    promos: PromoRules = PromoRules()
    addresses: AddressRules = AddressRules()

    def carrier_for_sender(self, address: str) -> dict[str, str] | None:
        domain = address.rsplit("@", 1)[-1].strip().lower().rstrip(">")
        best: SenderRule | None = None
        for rule in self.carriers.senders:
            if domain == rule.domain or domain.endswith("." + rule.domain):
                if best is None or len(rule.domain) > len(best.domain):
                    best = rule
        if best is None:
            return None
        return {"carrier": best.carrier, "display": best.display}

    def display_for_carrier(self, carrier: str | None) -> str | None:
        if not carrier:
            return None
        return self.carriers.display.get(carrier)


def _read_yaml(path: Path) -> dict:
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise RuleError(f"{path.name}: {exc}") from exc
    try:
        data = yaml.safe_load(raw) or {}
    except yaml.YAMLError as exc:
        raise RuleError(f"{path.name}: {exc}") from exc
    if not isinstance(data, dict):
        raise RuleError(f"{path.name}: expected a mapping at the top level")
    return data


def _tracking(path: Path, data: dict) -> TrackingRules:
    families = []
    for entry in data.get("families", []) or []:
        try:
            families.append(
                TrackingFamily(
                    id=str(entry["id"]),
                    pattern=str(entry["pattern"]),
                    carrier=entry.get("carrier"),
                    checksum=entry.get("checksum"),
                    confidence=float(entry.get("confidence", 0.5)),
                    needs_context=bool(entry.get("needs_context", False)),
                )
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise RuleError(f"{path.name}: bad family entry {entry!r}: {exc}") from exc
    return TrackingRules(
        families=tuple(families),
        context_words=tuple(data.get("context_words", []) or ()),
    )


def _carriers(path: Path, data: dict) -> CarrierRules:
    senders = []
    for entry in data.get("senders", []) or []:
        try:
            senders.append(
                SenderRule(
                    domain=str(entry["domain"]).lower(),
                    carrier=str(entry["carrier"]),
                    display=str(entry.get("display") or entry["carrier"]),
                )
            )
        except (KeyError, TypeError) as exc:
            raise RuleError(f"{path.name}: bad sender entry {entry!r}: {exc}") from exc
    display = {str(k): str(v) for k, v in (data.get("carrier_display") or {}).items()}
    return CarrierRules(senders=tuple(senders), display=display)


def _promos(path: Path, data: dict) -> PromoRules:
    return PromoRules(
        domains=tuple(str(d).lower() for d in data.get("domains", []) or ()),
        keywords=tuple(str(k) for k in data.get("keywords", []) or ()),
    )


def _addresses(path: Path, data: dict) -> AddressRules:
    labels = {
        str(kind): tuple(str(p) for p in patterns or ())
        for kind, patterns in (data.get("labels") or {}).items()
    }
    patterns = data.get("patterns") or {}
    return AddressRules(
        labels=labels,
        fallback_labels=tuple(str(p) for p in data.get("fallback_labels", []) or ()),
        street_then_number=str(patterns.get("street_then_number", "")),
        number_then_street=str(patterns.get("number_then_street", "")),
        postal_code=str(patterns.get("postal_code", "")),
    )


def load_rules(rules_dir: Path | str | None, seed_dir: Path | str | None) -> Rules:
    """Load a complete rule set. ``None`` for both means the packaged seeds."""
    if rules_dir is None:
        base = Path(seed_dir) if seed_dir else PACKAGE_RULES
    else:
        base = Path(rules_dir)
    return Rules(
        tracking=_tracking(base / "tracking.yaml", _read_yaml(base / "tracking.yaml")),
        carriers=_carriers(base / "carriers.yaml", _read_yaml(base / "carriers.yaml")),
        promos=_promos(base / "promos.yaml", _read_yaml(base / "promos.yaml")),
        addresses=_addresses(base / "addresses.yaml", _read_yaml(base / "addresses.yaml")),
    )


class RuleCache:
    """Live rules with a safe reload.

    On first load the live directory is seeded from the image. A malformed edit
    returns errors and keeps the previous rule set in place.
    """

    def __init__(self, rules_dir: Path | str, seed_dir: Path | str | None = None):
        self.rules_dir = Path(rules_dir)
        self.seed_dir = Path(seed_dir) if seed_dir else PACKAGE_RULES
        self._rules: Rules | None = None

    @property
    def rules(self) -> Rules:
        if self._rules is None:
            self.reload()
        assert self._rules is not None
        return self._rules

    def seed(self) -> None:
        self.rules_dir.mkdir(parents=True, exist_ok=True)
        for name in RULE_FILES:
            target = self.rules_dir / name
            if not target.exists():
                shutil.copyfile(self.seed_dir / name, target)

    def reload(self) -> list[str]:
        try:
            self.seed()
            rules = load_rules(self.rules_dir, self.seed_dir)
        except (RuleError, OSError) as exc:
            return [str(exc)]
        self._rules = rules
        return []
