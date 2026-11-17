# t07_closing_soon_gate: Date trigger with a gate that cancels small deals

Tier 2 · pipeline

Covers triggers: on_or_around_date  
Covers steps: condition, notify_member_via_text, stop_execution

## Prompt

Create an automation on the `eval_deals` pipeline named "{p} Closing Soon" with api_name `{p}_closing_soon`.

It runs 3 days before each deal's `close_date`, at 9:00 am. Then:

- If the deal's `amount` is 50000 or more: send a text message to the deal's owner saying "Closing soon: " followed by the deal's name (filled in from the record).
- Otherwise: stop the run and mark it as Cancelled.

## Rubric

- **date_trigger**: The trigger is 3 days before the field close_date, at 9:00 in the morning.
- **condition_amount**: The condition tests the field amount for 50000 or more.
- **yes_text**: The yes branch texts the record's owner "Closing soon: " followed by a merge field that resolves to the deal's name.
- **no_cancels**: The no branch stops the run and marks it Cancelled.
