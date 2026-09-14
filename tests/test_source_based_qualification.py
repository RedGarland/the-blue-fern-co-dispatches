from __future__ import annotations

from bluefern_dispatches.source_based_qualification import assess_review_retention


def test_food_line_retains_only_grocery_store_fire_access_loss() -> None:
    result = assess_review_retention(
        {
            "source_url": "https://example.org/spencer-shurfine-fire",
            "publisher": "FingerLakes1",
            "source_published_date": "2026-09-13",
            "evidence_text": (
                "A fire destroyed Spencer's only full-service grocery store, Shurfine Food Mart. "
                "Residents said the loss will make grocery shopping harder, especially for people unable to travel."
            ),
        },
        dispatch="food-line",
        edition_date="2026-09-14",
    )

    assert result["disposition"] == "retained_for_review"
    assert result["qualification_basis"] == "geographic access gap"


def test_food_line_retains_snap_losses_tied_to_emergency_food_demand() -> None:
    result = assess_review_retention(
        {
            "source_url": "https://azfoodbanks.org/visits-increase/",
            "publisher": "Arizona Food Bank Network",
            "source_published_date": "2026-06-30",
            "observed_date": "2026-09-14",
            "evidence_text": (
                "Arizona SNAP participation declined by more than 50 percent while food bank visits increased "
                "and emergency food providers reported more need."
            ),
        },
        dispatch="food-line",
        edition_date="2026-09-14",
    )

    assert result["disposition"] == "retained_for_review"
    assert result["qualification_basis"] == "benefit access failure"


def test_care_line_retains_it_disruption_clinic_cancellation() -> None:
    result = assess_review_retention(
        {
            "source_url": "https://fenwayhealth.org/care/medical/std-testing-services/",
            "publisher": "Fenway Health",
            "source_published_date": "2026-09-13",
            "evidence_text": (
                "Fenway Health continues to respond to an IT incident. Patient communications may be delayed, "
                "and the Sexual Health Walk-In Clinic is cancelled Friday morning."
            ),
        },
        dispatch="care-line",
        edition_date="2026-09-14",
    )

    assert result["disposition"] == "retained_for_review"
    assert result["qualification_basis"] == "service reduction"


def test_care_line_retains_future_effective_birth_center_delivery_pause() -> None:
    result = assess_review_retention(
        {
            "source_url": "https://www.atlantabirthcenter.org/transition",
            "publisher": "Atlanta Birth Center",
            "source_published_date": "2026-09-14",
            "evidence_text": (
                "Atlanta Birth Center will pause birth center deliveries effective September 30, 2026. "
                "Patients due later will transition birth plans while prenatal and postpartum care continue."
            ),
        },
        dispatch="care-line",
        edition_date="2026-09-14",
    )

    assert result["disposition"] == "retained_for_review"
    assert result["qualification_basis"] == "facility or service closure"
