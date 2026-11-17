# t03_account_tier_assign: Reassign an account a day after its tier changes

Tier 1 · custom object

Covers triggers: field_updated  
Covers steps: delay, assign_team_member

## Prompt

Create an automation on the `eval_accounts` object named "{p} Tier Reassign" with api_name `{p}_tier_reassign`.

When an account's `tier` field changes (any value to any value, but not when the record is first created), wait 1 day, then assign the account to a team member, rotating round-robin across all team members.

## Rubric

- **trigger_on_tier**: The trigger watches the field tier, for any change, and does not fire on record creation.
- **waits_one_day**: The wait is exactly 1 day.
- **round_robin_all**: The assignment rotates round-robin across all team members.
