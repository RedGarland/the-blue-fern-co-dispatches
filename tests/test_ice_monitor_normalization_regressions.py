from __future__ import annotations

import pytest

from bluefern_dispatches.ice_dispatch import _infer_category, _infer_location, currentness_for_dates


@pytest.mark.parametrize('text', [
    'Immigration and Customs Enforcement (ICE) announced the arrest of more worst of the worst criminal illegal aliens across the country, including those convicted of horrific crimes such as murder, predatory criminal sexual assault of a child, and aggravated assault.',
    'Immigration and Customs Enforcement (ICE) arrested more worst of the worst criminal illegal aliens, including those convicted for first-degree murder, statutory rape/statutory sodomy with a person less than 14 years old sexual assault, and other despicable crimes.',
    'ICE arrested a person convicted of a shooting years earlier.',
    'ICE officers arrested a person with a criminal history of firearm offenses.',
    'ICE arrests man charged with using force during a prior robbery.',
])
def test_arrest_history_and_agency_name_are_not_force_events(text):
    assert _infer_category(text) == 'enforcement'


def test_force_substrings_in_other_words_are_not_force_events():
    assert _infer_category('ICE announces a workforce enforcement operation.') != 'use_of_force'


@pytest.mark.parametrize('text', [
    'ICE announced the removal of a person convicted of firearm offenses.',
    'ICE announced the deportation of a person convicted of a shooting.',
    'ICE announced the detention of a person with a criminal history of firearm offenses.',
    'ICE will detain a person convicted of a shooting.',
])
def test_arrest_removal_nouns_do_not_attribute_conviction_history_to_agency_force(text):
    assert _infer_category(text) != 'use_of_force'


@pytest.mark.parametrize('text', [
    'An ICE officer charged after a Minnesota shooting was released on bond.',
    'An ICE officer was charged with a firearm offense after the encounter.',
    'Federal agents fired a firearm during an arrest.',
    'ICE officers reportedly used force while making an arrest.',
    'ICE officers allegedly deployed a taser during the encounter.',
    'ICE officers pursued a vehicle during a pursuit.',
    "ICE's use of force against a person convicted of robbery is under investigation.",
    'An ICE officer who used force against a convicted person faces review.',
    'ICE used force during an arrest of a person convicted of robbery.',
    'ICE officers were involved in shootings.',
    'ICE officers tasered a detainee.',
    'Federal agents were tasering people during the operation.',
])
def test_actual_or_alleged_agency_force_stays_reviewable(text):
    assert _infer_category(text) == 'use_of_force'


@pytest.mark.parametrize('text', [
    'The worst criminals have been removed to third countries, including murderers, pedophiles, rapists, and drug traffickers WASHINGTON – The United States Department of Homeland Security released a statement.',
    'WASHINGTON — DHS announced third-country deportations.',
    'WASHINGTON, D.C. - DHS announced a national policy.',
])
def test_newsroom_dateline_does_not_invent_event_geography(text):
    assert _infer_location(text) == {'location_precision': 'unknown'}


@pytest.mark.parametrize('text', [
    'ICE arrested people in Washington.',
    'ICE arrested people near WASHINGTON – officers provided a statement.',
    'WASHINGTON – ICE arrested people in Seattle, Washington.',
    'ICE transported people from WASHINGTON — witnesses provided statements.',
    'ICE conducted arrests throughout WASHINGTON — witnesses provided statements.',
    'An operation occurred in the state of WASHINGTON — witnesses provided statements.',
])
def test_explicit_washington_event_locations_are_preserved(text):
    assert _infer_location(text)['state_or_territory'] == 'WA'


def test_hyphenated_washington_description_is_not_a_dateline():
    result = _infer_location('ICE arrested a WASHINGTON-based organizer.')
    assert result['state_or_territory'] == 'WA'


def test_publication_date_does_not_manufacture_event_date():
    result = currentness_for_dates(published_at='2026-10-27T00:00:00Z', modified_at=None,
                                  event_date=None, retrieved_at='2026-10-29T04:15:02Z', window_hours=168)
    assert result == {'status': 'current_publication', 'confidence': 'date_supported'}
