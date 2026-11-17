# t22_enterprise_upgrade: Field trigger on one specific value, and an owner copied from a field

Tier 2 · custom object

Covers triggers: field_updated  
Covers steps: change_field_value, assign_team_member

## Prompt

Create an automation on the `eval_accounts` object named "{p} Enterprise Upgrade" with api_name `{p}_enterprise_upgrade`.

It runs only when an account's `tier` field changes to "Enterprise" (from any other value, blank included), and not when the record is first created. Steps, in order:

1. Set the account's `flagged` checkbox to checked.
2. Assign the account to the team member held in the account's own `account_manager` field.

## Rubric

- **trigger**: The trigger watches the field tier and fires only when it becomes the option "Enterprise", from any earlier value including blank, and not on record creation.
- **flags**: The first step sets flagged to checked.
- **assigns_from_field**: The second step assigns the record to the team member in the record's own account_manager field.
