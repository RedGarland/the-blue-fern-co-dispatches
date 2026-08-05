from bluefern_dispatches.dispatch_catalog import (
    active_dispatch_slugs,
    dispatch_is_active,
    dispatch_lifecycle_state,
)


def test_active_dispatch_slugs_are_lifecycle_controlled():
    assert active_dispatch_slugs() == ("gaza", "food-line", "care-line")
    assert dispatch_lifecycle_state("cascadia") == "future"
    assert dispatch_lifecycle_state("american-pressure") == "future"
    assert dispatch_lifecycle_state("gaza") == "active"
    assert dispatch_is_active("food-line") is True
    assert dispatch_is_active("cascadia") is False
