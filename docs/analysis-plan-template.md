# Analysis Plan Template

**Status:** Template only. Complete, date, and freeze this document before
looking at pilot outcomes or tuning on evaluation data. It is not a submitted
preregistration or ethics approval.

## Study record

- Project / protocol ID:
- Version and date frozen:
- Investigator(s):
- Dataset(s) and access agreement(s):
- Ethics review body and approval reference, if applicable:
- Intended claim: prototype validation on staged behavior / other (specify):

## Research question and comparison

- Primary question:
- Eye-only baseline definition (including threshold mode):
- Planned eye threshold ablations: fixed EAR / personalized threshold /
  personalized threshold plus bilateral quality gating (include only the arms
  approved for the protocol):
- Fusion system definition and channels enabled:
- Primary fusion comparison baseline (identify before outcomes; consider the
  strongest predeclared eye-only arm):
- Calibration procedure and who can access each split:
- Primary unit of analysis (participant / session / event):

## Outcomes

- Primary metric:
- Metric denominator and eligible observation time:
- Target operating point or matched-recall procedure:
- Secondary metrics (event recall, warning delay, false alarms per annotated
  negative hour, coverage, unknown time, subgroup performance):
- Minimum practically meaningful difference and rationale:
- Confidence interval method and resampling unit:
- Multiple-comparison handling, if applicable:

## Data and labels

- Inclusion and exclusion criteria:
- Event definitions and annotation source:
- Negative-time definition:
- Handling of ambiguous labels, missing channels, and incomplete sessions:
- Subject pseudonymization and data retention location/duration:
- Recording, feature-stream, and access permissions:

## Split and model development

- Subject-disjoint training IDs or deterministic assignment rule:
- Validation IDs / purpose:
- Calibration IDs / purpose:
- Held-out test IDs / access restriction:
- Feature selection and threshold tuning rules:
- Fusion fitting and probability calibration procedure:
- Rule for changes after the test set is opened:

## Safety and operations

- Staged stationary setup and supervisor:
- Stop criteria and participant withdrawal process:
- Confirmation that there is no sleep deprivation or public-road testing:
- Camera identity, placement, resolution, and measured frame rate:
- Incident / unexpected recording handling:

## Reporting commitments

- Report both favorable and null / unfavorable comparisons:
- Report coverage and unknown time alongside accuracy metrics:
- Report subject-level results and uncertainty, not frame-level significance:
- Describe staged behavior as staged behavior; do not claim real-world
  drowsiness detection from a staged pilot:
- Deviations from this plan and their dates:

## Freeze checklist

- [ ] Dataset access and licenses recorded.
- [ ] Required ethics review and consent completed before participant capture.
- [ ] Subject-disjoint split fixed before tuning.
- [ ] Primary metric, denominator, operating point, and meaningful margin fixed.
- [ ] Test-set access restricted until model and thresholds are frozen.
- [ ] Analysis code version and input hashes recorded.
- [ ] Deviations and null-result reporting plan documented.
