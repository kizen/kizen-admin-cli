# t10_overdue_goal: Wait for an event with a goal step

Tier 2 · custom object

Covers triggers: scheduled_activity_overdue  
Covers steps: goal, stop_execution, change_field_value

## Prompt

Create an automation on the `eval_tickets` object named "{p} Overdue Follow Up" with api_name `{p}_overdue_follow_up`.

It runs when a scheduled "Eval Follow Up" activity on a ticket becomes overdue. It then waits, with a goal step, for the ticket's `priority` field to be updated, for at most 2 days.

- If the priority is updated within that time (the goal is met): stop the run and mark it as Successfully Completed.
- If the 2 days pass without it: set the ticket's `escalated` checkbox to checked.

## Rubric

- **trigger**: The trigger is a scheduled "Eval Follow Up" activity becoming overdue.
- **goal**: The goal waits for the field priority to be updated, for at most 2 days.
- **met**: When the goal is met, the run stops and is marked Successfully Completed.
- **not_met**: When the time runs out, the field escalated is set to checked.
