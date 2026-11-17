# t04_ticket_intake_followup: Schedule a follow-up when a form is submitted

Tier 1 · custom object

Covers triggers: form_submitted  
Covers steps: schedule_activity

## Prompt

Create an automation on the `eval_tickets` object named "{p} Intake Follow Up" with api_name `{p}_intake_follow_up`.

When the form "Eval Ticket Intake" is submitted, schedule an "Eval Follow Up" activity due 1 day later, assigned to the ticket's owner, with a note that reads "New ticket from the intake form: " followed by the ticket's subject (filled in from the record).

## Rubric

- **form_is_intake**: The trigger is the submission of the form "Eval Ticket Intake".
- **activity_and_timing**: The step schedules an "Eval Follow Up" activity due 1 day after the step runs.
- **assigned_to_owner**: The activity is assigned to the record's owner.
- **note_content**: The note reads "New ticket from the intake form: " followed by a merge field that resolves to the ticket's subject.
