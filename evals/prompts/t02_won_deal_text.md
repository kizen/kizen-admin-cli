# t02_won_deal_text: Text the owner when a deal is won

Tier 1 · pipeline

Covers triggers: stage_updated  
Covers steps: notify_member_via_text

## Prompt

Create an automation on the `eval_deals` pipeline named "{p} Won Deal Text" with api_name `{p}_won_deal_text`.

When a deal moves into the stage "Closed Won" from any other stage, send a text message to the deal's owner (the team member who owns the record) that says "Deal won: " followed by the deal's name (filled in from the record when the text is sent).

## Rubric

- **stage_closed_won**: The trigger fires on a move into the stage "Closed Won", from any stage.
- **texts_the_owner**: The text goes to the record's owner, not to a fixed person or a role.
- **text_content**: The text reads "Deal won: " followed by a merge field that resolves to the deal's name when sent, not a literal name and not an empty or unsupported token.
