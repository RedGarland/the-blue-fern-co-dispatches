from bluefern_dispatches.food_line_sources import _food_line_map_eligible


def test_context_only_signal_cannot_be_mapped_even_when_keyword_classifier_fires() -> None:
    assert _food_line_map_eligible(
        pressure_signal=True,
        pressure_required=False,
        promotable=True,
        source_role_allowed="context_only",
        source_role="local_signal",
        supported_geography=True,
        state="FL",
    ) is False


def test_verified_required_local_pressure_signal_remains_map_eligible() -> None:
    assert _food_line_map_eligible(
        pressure_signal=True,
        pressure_required=True,
        promotable=True,
        source_role_allowed="pressure_evidence",
        source_role="local_signal",
        supported_geography=True,
        state="WA",
    ) is True


def test_resource_or_nonpromotable_signal_cannot_be_mapped() -> None:
    assert _food_line_map_eligible(
        pressure_signal=True,
        pressure_required=True,
        promotable=False,
        source_role_allowed="pressure_evidence",
        source_role="resource_context",
        supported_geography=True,
        state="TX",
    ) is False
