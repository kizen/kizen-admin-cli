# t30_goal_stage_wait: A goal with no time limit, and an activity due relative to a date field

Tier 2 · pipeline

Covers triggers: activity_logged  
Covers steps: goal, schedule_activity

## Prompt

Create an automation on the `eval_deals` pipeline named "{p} Won Follow Up" with api_name `{p}_won_follow_up`.

It runs when an "Eval Call" activity is logged on a deal. It then waits, with a goal step and no time limit, for the deal to move into the stage "Closed Won".

When that happens (the goal is met), it schedules an "Eval Follow Up" activity due 1 day after the deal's `close_date`, at 10:00, assigned round-robin across all team members.

## Rubric

- **trigger**: The trigger is an "Eval Call" activity being logged.
- **goal**: The goal waits, with no time limit, for a move into the stage "Closed Won".
- **activity**: When the goal is met, an "Eval Follow Up" activity is scheduled due 1 day after the field close_date, at 10:00, assigned round-robin across all team members.
