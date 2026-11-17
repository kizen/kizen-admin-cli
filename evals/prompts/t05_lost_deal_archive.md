# t05_lost_deal_archive: Archive a lost deal after 30 days

Tier 1 · pipeline

Covers triggers: stage_updated  
Covers steps: delay, archive_record

## Prompt

Create an automation on the `eval_deals` pipeline named "{p} Lost Deal Archive" with api_name `{p}_lost_deal_archive`.

When a deal moves into the stage "Closed Lost", wait 30 days, then archive the deal record itself.

## Rubric

- **stage_closed_lost**: The trigger fires on a move into the stage "Closed Lost".
- **waits_thirty_days**: The wait is exactly 30 days.
- **archives_this_deal**: The archive step archives the triggering deal itself, not a related record.
