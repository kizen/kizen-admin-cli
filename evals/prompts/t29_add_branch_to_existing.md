# t29_add_branch_to_existing: Edit a condition and add a step on its other branch

Tier 2 · contacts

Covers triggers: manual  
Covers steps: condition, change_field_value

## Before the session

- Create the automation in `setup/score_gate.json` first, and leave it inactive.

## Prompt

An automation `{p}_score_gate` already exists on Contacts. It checks whether the contact's `eval_lead_score` is 50 or more, and if so sets the contact's `eval_lifecycle` to "Engaged". Its other branch is empty.

Change it in place. Do not delete and recreate it, and do not replace its existing steps with new ones: the two steps it has now must keep their identity, so their run history stays attached.

1. Change the threshold from 50 to 75.
2. On the branch taken when the score is below the threshold, add a step that sets the contact's `eval_lifecycle` to "Dormant".

## Rubric

- **threshold**: The condition now tests eval_lead_score for 75 or more.
- **yes_intact**: The yes branch still sets eval_lifecycle to "Engaged".
- **no_dormant**: The no branch sets eval_lifecycle to "Dormant".
- **kept_identity**: The condition step and the existing field-change step have the same ids they had before the session (compare with the automation as it was set up).
