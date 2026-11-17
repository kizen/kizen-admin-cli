# t21_remove_and_activate: Remove a step from an existing automation and activate it

Tier 2 · contacts

Covers triggers: manual  
Covers steps: change_field_value

## Before the session

- Create the automation in `setup/nurture.json` first, and leave it inactive.

## Prompt

An automation `{p}_nurture` already exists on Contacts and is inactive. It waits 1 day and then sets the contact's `eval_lifecycle` to "Engaged".

Change it in place, without deleting and recreating it: remove the 1-day wait, so the field change is the only step and keeps its identity. Then activate the automation.

## Rubric

- **only_step**: The automation's only step sets eval_lifecycle to "Engaged".
- **active**: The automation is active.
- **kept_identity**: The field-change step has the same id it had before the session (compare with the automation as it was set up).
