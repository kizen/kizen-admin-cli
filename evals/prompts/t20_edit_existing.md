# t20_edit_existing: Edit and insert steps in an existing automation

Tier 2 · contacts

Covers triggers: manual  
Covers steps: delay, notify_member_via_text, change_field_value

## Before the session

- Create the automation in `setup/nurture.json` first, and leave it inactive.

## Prompt

An automation `{p}_nurture` already exists on Contacts. It waits 1 day and then sets the contact's `eval_lifecycle` to "Engaged".

Change it in place. Do not delete and recreate it, and do not replace its existing steps with new ones: the two steps it has now must keep their identity, so their run history stays attached.

1. Change the wait from 1 day to 3 days.
2. Insert a new step between the wait and the field change: a text message to the contact's owner saying "Nurture: ", then the contact's first name (filled in from the record), then " is ready".

## Rubric

- **wait_three_days**: The wait is now exactly 3 days.
- **text_step**: Between the wait and the field change there is a text to the record's owner reading "Nurture: ", then a merge field that resolves to the contact's first name, then " is ready".
- **field_change_intact**: The last step still sets eval_lifecycle to "Engaged".
- **kept_identity**: The wait step and the field-change step have the same ids they had before the session (compare with the automation as it was set up).
