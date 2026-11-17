# t25_secured_webhook: Header-authenticated webhook, and a code step with a fixed input and a variable output

Tier 3 · custom object

Covers triggers: webhook  
Covers steps: initialize_variable, code_step, condition, change_field_value

## Before the session

- Put `assets/tag_ticket.py` in the session's working folder.

## Prompt

Create an automation on the `eval_tickets` object named "{p} Secured Webhook" with api_name `{p}_secured_webhook`.

It is started by a webhook named `{p}_inbound`. The webhook only accepts requests that carry the HTTP header `X-Eval-Token` with the value `evaltoken123` (store that value literally; this is a test value, not a real secret).

It uses one string variable, `ticket_tag`. Steps, in order:

1. A code step running the Python script in `tag_ticket.py` (in this folder), unchanged. It has two inputs: `subject`, bound to the ticket's `subject` field, and `mode`, which is always the fixed text "strict". It has one output, `tag`, which writes the variable `ticket_tag`.
2. Check whether the variable `ticket_tag` equals "vip".
   - If yes: set the ticket's `priority` to "Urgent".
   - If no: do nothing.

## Rubric

- **webhook**: The webhook trigger is named with the run's prefix followed by "_inbound" and requires the HTTP header X-Eval-Token with the value evaltoken123.
- **code_step**: The code step's script is the provided tag_ticket.py, unchanged. Its input subject is bound to the field subject, its input mode is the fixed text "strict", and its output tag writes the variable ticket_tag.
- **condition**: The condition tests whether the variable ticket_tag equals "vip".
- **yes_urgent**: The yes branch sets priority to "Urgent", and the no branch has no step.
