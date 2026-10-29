"""Offline regression cases; synthetic examples are not source reporting."""
from datetime import date
from pathlib import Path
import socket
import unittest
from unittest.mock import patch

import bluefern_dispatches.care_line_national_pipeline as pipeline
from bluefern_dispatches.care_line_source_registry import CareLineSource

# Exact short deadline clause preserved in the saved Care review evidence.
SAVED_DEADLINE_CLAUSE = (
    "The senators called on the insurance companies to provide responses to "
    "several questions by November 6"
)
# Synthetic continuation supplies a historical event mention, not a new factual claim.
DEADLINE_PASSAGE = SAVED_DEADLINE_CLAUSE + ", including why the companies terminated ACA coverage."
SAVED_UNSUPPORTED_ENTITY = "in Financial Gains Mayo Clinic"


def currentness(text):
    return pipeline._currentness_analysis(
        title="Senators investigate ACA coverage terminations",
        lead_text="Questions about prior ACA coverage terminations.",
        text=text,
        source_publication_date="2026-10-28",
        event_type="coverage_cancellation",
        service_line="coverage_access",
    )


def source():
    return CareLineSource.model_validate({
        "source_id": "fixture", "name": "Fixture News", "publisher": "Fixture News",
        "source_type": "trade_publication", "feed_url": "https://example.invalid/feed",
        "homepage_url": "https://example.invalid/", "state": "", "geographic_scope": "national",
        "organization_type": "trade_publication", "care_line_topics": ["ACA"],
        "authority_level": "secondary", "expected_update_frequency": "daily", "enabled": True,
        "adapter_type": "rss", "requires_html_followup": False,
        "source_role": "healthcare_access_reporting", "historical_depth": "current feed",
        "item_permalink_available": True, "created_at": "2026-10-28T00:00:00Z",
        "updated_at": "2026-10-28T00:00:00Z",
    })


class CareDeadlineEntityGuards(unittest.TestCase):
    def setUp(self):
        self.network = patch.object(socket.socket, "connect", side_effect=AssertionError("offline test network forbidden"))
        self.network.start()
        self.addCleanup(self.network.stop)

    def test_ci_selects_pipeline_and_guard_suites(self):
        workflow = (Path(__file__).resolve().parents[1] / ".github/workflows/dispatch-validation.yml").read_text()
        self.assertIn("tests/test_care_line_national_pipeline.py", workflow)
        self.assertIn("tests/test_care_line_deadline_entity_guards.py", workflow)

    def test_saved_response_deadline_is_not_coverage_effective_date(self):
        result = currentness(DEADLINE_PASSAGE)
        self.assertNotEqual(result["currentness_class"], "CURRENT_ANNOUNCEMENT_FUTURE_EFFECTIVE")
        self.assertEqual(result["event_effective_date"], "")
        self.assertEqual(result["operative_event_date"], "")
        self.assertEqual(result["currentness_class"], "DATE_UNRESOLVED")
        self.assertEqual(result["freshness_role"], "UNKNOWN")

    def test_response_deadline_variants_do_not_become_effective_dates(self):
        for sentence in (
            "Insurers must respond by November 6 about ACA coverage they terminated.",
            "Responses are due on November 6 concerning terminated ACA coverage.",
            "Responses are due November 6 concerning terminated ACA coverage.",
            "The response deadline is November 6 concerning terminated ACA coverage.",
            "Senators requested responses about terminated ACA coverage by November 6.",
            "Insurers must respond to questions about canceled ACA coverage on November 6.",
            "The November 6 response deadline concerns terminated ACA coverage.",
            "The deadline for responses is November 6 concerning terminated ACA coverage.",
            "The November 6 deadline for responses concerns terminated ACA coverage.",
            "Insurers must provide answers before Nov. 6, 2026 about terminated ACA coverage.",
        ):
            with self.subTest(sentence=sentence):
                self.assertEqual(currentness(sentence)["event_effective_date"], "")

    def test_distinct_cancellation_date_survives_deadline_same_sentence(self):
        for text in (
            "Responses are due by November 6; the insurer will terminate ACA coverage on November 12.",
            "The insurer will terminate ACA coverage on November 12; responses are due by November 6.",
            "Responses explain why the insurer will terminate ACA coverage on November 12.",
            "Responses explain why the insurer terminates ACA coverage on November 12.",
            "Responses explain why the insurer plans to terminate ACA coverage on November 12.",
            "Responses explain why ACA coverage is canceled on November 12.",
            "Responses are due on November 6; the insurer will terminate ACA coverage on November 12.",
            "Responses are due on November 6 and the insurer will terminate ACA coverage on November 12.",
            "On October 28 senators requested responses by November 6 and the insurer will terminate ACA coverage on November 12.",
            "Responses are due on November 6 about terminated ACA coverage. The insurer will terminate ACA coverage on November 12.",
            "Responses are due on November 6, 2026; the insurer will terminate ACA coverage on November 12, 2026.",
        ):
            with self.subTest(text=text):
                result = currentness(text)
                self.assertEqual(result["currentness_class"], "CURRENT_ANNOUNCEMENT_FUTURE_EFFECTIVE")
                self.assertEqual(result["event_effective_date"], "2026-11-12")

    def test_oversight_today_and_year_are_not_cancellation_dates(self):
        for text in (
            "Senators said today that responses are due by November 6 about terminated ACA coverage.",
            "Responses about the 2026 investigation of terminated ACA coverage are due by November 6.",
            "CMS has not canceled ACA coverage today; responses are due by November 6.",
            "On October 28, senators requested responses by November 6 about terminated ACA coverage.",
            "Senators scheduled a hearing on November 2 and requested responses by November 6 about terminated ACA coverage.",
            "If CMS canceled ACA coverage today; responses are due by November 6.",
            "The insurer may terminate ACA coverage on May 6, 2027; responses are due by November 6.",
        ):
            with self.subTest(text=text):
                result = currentness(text)
                self.assertEqual(result["currentness_class"], "DATE_UNRESOLVED")
                self.assertEqual(result["operative_event_date"], "")
                self.assertEqual(result["event_effective_date"], "")

    def test_independent_current_access_clause_keeps_its_date(self):
        result = currentness("CMS canceled ACA coverage today; responses are due by November 6.")
        self.assertEqual(result["currentness_class"], "CURRENT_EVENT")
        self.assertEqual(result["operative_event_date"], "2026-10-28")
        self.assertEqual(result["event_effective_date"], "")

    def test_same_date_can_independently_support_real_cancellation(self):
        result = currentness("Responses are due on November 6; the insurer will terminate ACA coverage on November 6.")
        self.assertEqual(result["event_effective_date"], "2026-11-06")

    def test_real_dated_cancellation_is_preserved(self):
        for text, expected in (
            ("The insurer will terminate ACA coverage on November 12.", "2026-11-12"),
            ("Responses are due by November 6; the insurer will terminate ACA coverage on May 6, 2027.", "2027-05-06"),
        ):
            with self.subTest(text=text):
                self.assertEqual(currentness(text)["event_effective_date"], expected)

    def test_structured_coverage_provider_is_preserved(self):
        subject, provider, provenance = pipeline._extract_subject(
            {"provider_name": "Fixture Health Plan"}, "Coverage update", DEADLINE_PASSAGE,
            service_line="coverage_access", evidence_text=SAVED_UNSUPPORTED_ENTITY,
        )
        self.assertEqual(provider, "Fixture Health Plan")
        self.assertEqual(provenance["provider_name"].source_field, "structured_input")

    def test_bounded_subject_and_noncoverage_body_recovery_are_preserved(self):
        for service_line, title, passage, body in (
            ("coverage_access", "Example Clinic cancels coverage", "Example Clinic cancels ACA coverage.", "Unrelated text"),
            ("", "Access update", "", "Riverside Clinic will suspend services."),
        ):
            with self.subTest(service_line=service_line):
                subject, provider, provenance = pipeline._extract_subject({}, title, passage, service_line=service_line, evidence_text=body)
                self.assertTrue(subject or provider)
                self.assertTrue(provenance)

    def qualify_fixture(self, passage=DEADLINE_PASSAGE, structured=None):
        raw = {
            "raw_item_id": "fixture-raw", "source_id": "fixture", "source_name": "Fixture News",
            "source_publisher": "Fixture News", "source_geographic_scope": "national",
            "item_url": "https://example.invalid/story", "title": "Senators investigate ACA coverage terminations",
            "description": passage, "content_text": passage,
            "source_date_state": "source_dated", "source_publication_date": "2026-10-28",
            "discovery_date": "2026-10-28",
        }
        raw.update(structured or {})
        article = {
            "title": raw["title"], "description": passage,
            "text": passage + " " + SAVED_UNSUPPORTED_ENTITY,
            "extraction_outcome": "BODY_EXTRACTED", "extraction_method": "synthetic_fixture",
        }
        with patch.object(pipeline, "fetch_url", return_value=(b"fixture", {})), patch.object(pipeline, "_extract_article_content", return_value=article):
            status, result = pipeline.qualify_event_lead(
                source(), raw, pipeline.event_lead_from_raw_item(raw), artifact_path="fixture.raw-items.json",
                run_id="fixture-run", fetch_timeout=1, allow_insecure_tls=False,
            )
        return status, result

    def test_calendar_year_is_not_a_second_january_event(self):
        result = currentness("The insurer will terminate ACA coverage on November 12, 2026.")
        self.assertEqual(result["event_effective_date"], "2026-11-12")
        refs = pipeline._explicit_dates_from_text("The clinic closed in 2020.", source_date=date(2026, 10, 28))
        self.assertEqual(refs[0]["kind"], "year_reference")
        self.assertEqual(refs[0]["relation"], "historical")

    def test_unrelated_entity_in_full_coverage_evidence_is_not_promoted(self):
        status, result = self.qualify_fixture("The insurer terminated ACA coverage for enrollees.")
        self.assertEqual(status, "qualified", result)
        self.assertEqual(result["normalized_record"]["provider_name"], "ACA Marketplace coverage")

    def test_structured_provider_survives_coverage_pipeline(self):
        status, result = self.qualify_fixture(structured={"provider_name": "Fixture Health Plan"})
        self.assertEqual(status, "qualified", result)
        self.assertEqual(result["normalized_record"]["provider_name"], "Fixture Health Plan")

    def test_investigation_keeps_existing_qualified_private_review_route(self):
        status, result = self.qualify_fixture()
        self.assertEqual(status, "qualified", result)
        record = result["normalized_record"]
        self.assertEqual(record["effective_date"], "")
        self.assertEqual(record["source_publication_date"], "2026-10-28")
        self.assertEqual(result["qualification_result"]["operative_event_date"], "")
        self.assertEqual(result["qualification_result"]["freshness_role"], "UNKNOWN")
        self.assertEqual(record["provider_name"], "ACA Marketplace coverage")
        self.assertEqual(record["field_provenance"]["provider_name"]["review_status"], "proposed")
        self.assertEqual(record["review_status"], "not_reviewed")
        self.assertEqual(record["public_status"], "not_public")
        self.assertFalse(result["qualification_result"]["public_eligibility_precheck"])
        self.assertIn("coverage_cancellation_editorial_framing_required", result["qualification_result"]["review_warnings"])
        queue = pipeline.build_review_queue([dict(result, candidate_status="active")], edition_date="2026-10-28")
        self.assertEqual(len(queue["items"]) + len(queue["backlog"]), 1)
        row = (queue["items"] + queue["backlog"])[0]
        self.assertNotIn("title", row)  # Existing queue contract intentionally has no title key.


if __name__ == "__main__":
    unittest.main(verbosity=2)
