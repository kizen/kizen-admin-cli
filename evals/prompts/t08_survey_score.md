# t08_survey_score: Survey trigger with a numeric condition

Tier 2 · contacts

Covers triggers: survey_submitted  
Covers steps: condition, change_field_value

## Prompt

Create an automation on Contacts named "{p} Survey Score" with api_name `{p}_survey_score`.

It runs when the survey "Eval Contact Survey" is submitted. If the contact's `eval_lead_score` is 50 or more, set the contact's `eval_lifecycle` to "Engaged". Otherwise do nothing.

## Rubric

- **survey_trigger**: The trigger is the submission of the survey "Eval Contact Survey".
- **condition_score**: The condition tests the field eval_lead_score for 50 or more.
- **yes_engaged**: The yes branch sets eval_lifecycle to "Engaged", and the no branch has no step.
