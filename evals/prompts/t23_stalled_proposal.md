# t23_stalled_proposal: Stage-to-stage trigger, today's date, a stage check, a stage move

Tier 2 · pipeline

Covers triggers: stage_updated  
Covers steps: initialize_variable, change_field_value, delay, condition, archive_record

## Prompt

Create an automation on the `eval_deals` pipeline named "{p} Stalled Proposal" with api_name `{p}_stalled_proposal`.

It runs when a deal moves from the stage "Qualifying" to the stage "Proposal". Steps, in order:

1. Set the deal's `proposal_sent` date field to the date the run started (today's date, not a fixed date).
2. Wait 7 days.
3. Check whether the deal is still in the stage "Proposal".
   - If it is: move the deal to the stage "Closed Lost", and then archive the ticket held in the deal's `onboarding_ticket` relationship field (archive that related ticket, not the deal).
   - If it is not: do nothing.

## Rubric

- **trigger**: The trigger fires on a move from the stage "Qualifying" to the stage "Proposal".
- **stamps_today**: The field proposal_sent is set from a variable that holds the run's start date, not from a fixed date.
- **waits_seven_days**: The wait is exactly 7 days.
- **checks_stage**: The condition tests whether the stage is "Proposal".
- **yes_branch**: The yes branch moves the deal to the stage "Closed Lost" and then archives the related ticket reached through the field onboarding_ticket, not the deal.
