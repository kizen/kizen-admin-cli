# t06_call_outcome_branch: Branch on what a logged call recorded

Tier 2 · pipeline

Covers triggers: activity_logged  
Covers steps: condition, schedule_activity, change_field_value

## Prompt

Create an automation on the `eval_deals` pipeline named "{p} Call Outcome" with api_name `{p}_call_outcome`.

It runs when an "Eval Call" activity is logged on a deal. Then it checks the deal's `call_outcome` field:

- If it is "Left Voicemail" or "No Answer": schedule an "Eval Follow Up" activity due in 2 days, assigned to the deal's owner.
- Otherwise: set the deal's `next_step` field to "Send recap".

## Rubric

- **trigger_eval_call**: The trigger is an "Eval Call" activity being logged.
- **condition_values**: The condition tests the field call_outcome for exactly the two options "Left Voicemail" and "No Answer".
- **yes_branch**: The yes branch schedules an "Eval Follow Up" activity due in 2 days, assigned to the record's owner.
- **no_branch**: The no branch sets the field next_step to "Send recap".
