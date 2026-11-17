# t11_weekly_account_sweep: Scheduled global automation that starts another on each match

Tier 2 · global

Covers triggers: schedule  
Covers steps: initialize_variable, search_records, start_automation

## Before the session

- Create the automation in `setup/account_review.json` first and activate it.

## Prompt

An automation `{p}_account_review` already exists on the `eval_accounts` object and is active.

Create a global automation (one with no target object) named "{p} Weekly Sweep" with api_name `{p}_weekly_sweep`.

It runs every Monday at 13:00 UTC. Each run searches the `eval_accounts` object for every account whose `flagged` checkbox is checked, and starts `{p}_account_review` on each account found.

## Rubric

- **schedule**: The schedule is weekly, on Mondays, at 13:00 UTC.
- **search**: The search looks in eval_accounts for records whose flagged checkbox is checked, and writes them to a list variable.
- **starts_review**: The start step starts the automation whose api_name is the run's prefix followed by "_account_review", once on each record in that list variable.
