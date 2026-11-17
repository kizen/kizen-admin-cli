# t16_deal_won_onboarding: Parallel lanes that create and modify related records, then rejoin

Tier 3 · pipeline

Covers triggers: stage_updated  
Covers steps: branch, create_related_entity, modify_related_entities, merge_branches, change_field_value

## Prompt

Create an automation on the `eval_deals` pipeline named "{p} Won Onboarding" with api_name `{p}_won_onboarding`.

It runs when a deal moves into the stage "Closed Won". It then does two things in parallel (two lanes of a parallel branch):

- Lane A: create a related record on the `eval_tickets` object named "Onboarding: " followed by the deal's name (filled in from the deal), owned by the same owner as the deal, and linked to the deal through the deal's `onboarding_ticket` relationship field. Then, in a second step, set that new ticket's `priority` to "Urgent".
- Lane B: on the account the deal is related to through its `account` relationship field (an `eval_accounts` record), set `flagged` to checked.

After both lanes finish they rejoin (use a merge), and the deal's `next_step` field is set to "Kick off onboarding".

## Rubric

- **trigger**: The trigger fires on a move into the stage "Closed Won".
- **creates_ticket**: One lane creates a record on eval_tickets named "Onboarding: " followed by a merge field that resolves to the deal's name, owned by the deal's owner, and linked through the field onboarding_ticket.
- **ticket_urgent**: The next step in that lane sets priority to "Urgent" on the ticket reached through onboarding_ticket.
- **flags_account**: The other lane sets flagged to checked on the account reached through the field account.
- **after_merge**: After the merge, the field next_step is set to "Kick off onboarding".
- **parallel_lanes**: The two lanes hang off one parallel branch step and rejoin at a merge step, with nothing duplicated after it.
