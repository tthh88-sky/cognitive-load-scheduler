from datetime import time

import pytest
from pydantic import ValidationError

from app.preferences import Preferences


def test_defaults_validate():
    prefs = Preferences()
    assert prefs.workdays == ["Mon", "Tue", "Wed", "Thu", "Fri"]
    assert prefs.workday_start == time(9, 0)
    assert prefs.workday_end == time(19, 0)


def test_energy_at_clamps_to_defined_range():
    prefs = Preferences()
    assert prefs.energy_at(8) == prefs.energy_curve[9]
    assert prefs.energy_at(20) == prefs.energy_curve[18]


def test_end_before_start_raises():
    with pytest.raises(ValidationError):
        Preferences(workday_start=time(19, 0), workday_end=time(9, 0))


def test_start_off_15_minute_boundary_raises():
    with pytest.raises(ValidationError):
        Preferences(workday_start=time(9, 10))


def test_day_modifier_cap_overrides_default():
    from app.preferences import DayModifier

    prefs = Preferences(day_modifiers={"Mon": DayModifier(max_deep_minutes=60)})
    assert prefs.deep_cap("Mon") == 60
    assert prefs.deep_cap("Tue") == prefs.max_deep_minutes_per_day
