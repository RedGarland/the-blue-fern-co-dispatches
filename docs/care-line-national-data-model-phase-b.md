# Care Line national data model and taxonomy

This document defines the Phase B canonical Care Line event model used for reviewed records, Universal Events shadow ingestion, release gating, and Signal Wire compatibility.

Owning module:

- `src/bluefern_dispatches/care_line_record.py`

The goal is one canonical reviewed-record contract with controlled normalization and validation, while keeping existing June 19 dispatch behavior and July 24 Signal Wire output unchanged.

## Canonical reviewed record

`CareLineReviewedRecord` remains the canonical reviewed event schema.

It now owns:

- jurisdiction normalization
- geographic scope normalization
- event-type normalization
- service-line normalization
- access-consequence normalization
- verification-state normalization
- workflow-state normalization
- date-semantic fields
- publication-eligibility validation
- deterministic public-location labeling
- private-path exclusion from deterministic/public-facing serialization

Legacy lower-snake values remain accepted for backward compatibility. The model stores legacy-compatible values for current integration points and also records the canonical taxonomy value used by Phase B validation.

## Jurisdiction model

Supported jurisdictions: 56

- 50 states
- District of Columbia
- Puerto Rico
- Guam
- U.S. Virgin Islands
- Northern Mariana Islands
- American Samoa

Each jurisdiction has:

- canonical name
- canonical display label
- two-letter code
- jurisdiction type: `STATE`, `FEDERAL_DISTRICT`, or `TERRITORY`
- country fixed as `United States`
- accepted aliases

Important rule:

- `Washington` normalizes to Washington state.
- DC requires an explicit DC-form alias such as `District of Columbia`, `Washington, DC`, `Washington D.C.`, or `DC`.

National scope remains allowed as `US` only when the record is explicitly national rather than jurisdiction-specific.

## Geographic scope model

Controlled canonical scopes:

- `FACILITY`
- `LOCALITY`
- `COUNTY_EQUIVALENT`
- `MULTI_COUNTY`
- `SERVICE_REGION`
- `JURISDICTION_WIDE`
- `MULTI_JURISDICTION`
- `NATIONAL`
- `TRIBAL_SERVICE_AREA`

The record contract also supports:

- facility name
- locality/city
- county-equivalent text
- jurisdiction
- postal code
- service region
- tribal nation
- tribal service area
- island
- rural/urban/frontier evidence metadata
- verified coordinates
- deterministic public location label
- map eligibility independent from jurisdiction validity

Territory localities are not forced into a county field.

## Event taxonomy

Canonical Phase B event taxonomy:

- `FACILITY_CLOSURE`
- `TEMPORARY_FACILITY_CLOSURE`
- `SERVICE_LINE_CLOSURE`
- `SERVICE_SUSPENSION`
- `REDUCED_HOURS`
- `REDUCED_CAPACITY`
- `STAFFING_RESTRICTION`
- `DIVERSION`
- `RELOCATION`
- `CONSOLIDATION`
- `BANKRUPTCY_RELATED_SERVICE_LOSS`
- `OWNERSHIP_TRANSITION`
- `ACCESS_RESTRICTION`
- `REOPENING`
- `SERVICE_RESTORATION`

Backward-compatible legacy values still accepted by the schema include:

- `planned_facility_closure`
- `temporary_facility_suspension`
- `ownership_change`
- `operator_change`
- `facility_conversion`
- `service_expansion`

Those values normalize to the nearest controlled Phase B category without changing current public output.

## Service-line taxonomy

Canonical Phase B service-line taxonomy:

- `EMERGENCY`
- `MATERNITY`
- `LABOR_AND_DELIVERY`
- `INPATIENT`
- `OUTPATIENT`
- `PRIMARY_CARE`
- `URGENT_CARE`
- `BEHAVIORAL_HEALTH`
- `SUBSTANCE_USE_TREATMENT`
- `PEDIATRICS`
- `ONCOLOGY`
- `DIALYSIS`
- `PHARMACY`
- `DENTAL`
- `IMAGING`
- `LABORATORY`
- `SURGERY`
- `REHABILITATION`
- `HOME_HEALTH`
- `HOSPICE`
- `AMBULANCE_EMS`
- `LONG_TERM_CARE`
- `SKILLED_NURSING`
- `TRIBAL_HEALTH`
- `VETERANS_HEALTH`
- `SPECIALTY_CARE`
- `MULTIPLE_SERVICES`
- `ENTIRE_FACILITY`

Alias examples:

- `ER`, `ED`, `emergency department` -> `EMERGENCY`
- `labor & delivery`, `L&D` -> `LABOR_AND_DELIVERY`
- `mental health` -> `BEHAVIORAL_HEALTH`
- `EMS`, `ambulance` -> `AMBULANCE_EMS`

## Access-consequence taxonomy

Controlled values:

- `LOSS_OF_LOCAL_ACCESS`
- `LONGER_TRAVEL_DISTANCE`
- `REDUCED_SERVICE_AVAILABILITY`
- `REDUCED_OPERATING_HOURS`
- `REDUCED_BED_OR_APPOINTMENT_CAPACITY`
- `EMERGENCY_DIVERSION`
- `DELAYED_CARE_RISK`
- `TRANSFER_DEPENDENCE`
- `WORKFORCE_RELATED_RESTRICTION`
- `SUBSTITUTE_SERVICE_OFFERED`
- `NO_CONFIRMED_ACCESS_CONSEQUENCE`

Validation rules:

- unsupported values fail closed
- `LONGER_TRAVEL_DISTANCE` requires sourced support
- `NO_CONFIRMED_ACCESS_CONSEQUENCE` cannot support a publishable pressure signal by itself

## Verification and workflow state

Verification state is distinct from workflow state.

Verification:

- `DISCOVERED`
- `SOURCE_VERIFIED`
- `CORROBORATED`
- `AUTHORITY_CONFIRMED`
- `DISPUTED`
- `INSUFFICIENT_EVIDENCE`

Workflow:

- `NEW`
- `NEEDS_REVIEW`
- `APPROVED`
- `EXCLUDED`
- `DUPLICATE`
- `SUPERSEDED`
- `PUBLISHED`

Publish-gating rules:

- approved workflow plus disputed/insufficient verification fails closed
- queue eligibility now respects workflow and verification separately

## Date semantics

Separate fields are supported for:

- source publication date
- announcement date
- effective date
- observed date
- review date
- publication date

Each date field can carry independent precision. No publication time is invented by the schema layer.

## Public/private field separation

The canonical model strips or rejects private filesystem provenance in deterministic/public-facing serialization.

Examples blocked:

- absolute reviewed-record paths
- absolute publication-state links
- path-shaped private metadata in deterministic output

## Backward compatibility defaults

Phase B intentionally preserves current public artifacts.

Compatibility defaults include:

- legacy event values remain accepted and normalize to a controlled Phase B category
- legacy service-line values remain accepted and normalize to a controlled Phase B service line
- June national records may retain `state=US` when the scope is explicitly national
- missing new optional fields receive deterministic empty/default values
- stronger public or evidence claims are never inferred from missing legacy fields

## Phase boundaries

Phase B is schema and taxonomy only.

It does not:

- expand the source registry
- change live public Care Line content
- modify Pages output
- authorize publication
