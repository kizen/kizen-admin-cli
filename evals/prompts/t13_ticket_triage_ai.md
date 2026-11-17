# t13_ticket_triage_ai: File extraction, an AI decision, and branches that rejoin

Tier 3 · custom object

Covers triggers: manual  
Covers steps: file_content_extraction, condition, change_field_value, call_llm, merge_branches, notify_member_via_text

## Prompt

Create an automation on the `eval_tickets` object named "{p} Ticket Triage" with api_name `{p}_ticket_triage`. It is started manually. Use the `kizen/lite` model for every AI step.

Steps, in order:

1. A file content extraction step that reads the ticket's `attachment` file field with the prompt "Summarize this attachment in two sentences." and writes the result to the ticket's `summary` field.
2. An AI decision (a condition decided by the model, not by a filter) whose prompt is "Is this ticket about an urgent problem? Subject: " followed by the ticket's subject, filled in from the record.
   - If yes: set the ticket's `priority` to "Urgent".
   - If no: an AI step whose prompt is "Classify this ticket as Billing, Technical or Other. Answer with the category only. Message: " followed by the ticket's message, filled in from the record. It writes its answer to the ticket's `ticket_category` field.
3. The two branches rejoin (use a merge, do not duplicate the next step), and then a text message is sent to the ticket's owner saying "Triage finished".

For both of those prompts, a blank value should default to "Unknown" rather than fail the step.

## Rubric

- **extraction**: The extraction step reads the field attachment, uses the prompt "Summarize this attachment in two sentences.", and writes to the field summary.
- **decision**: The condition is an AI decision whose prompt is "Is this ticket about an urgent problem? Subject: " followed by a merge field that resolves to the ticket's subject.
- **yes_urgent**: The yes branch sets priority to "Urgent".
- **no_classifies**: The no branch is an AI step whose prompt asks for Billing, Technical or Other and includes a merge field that resolves to the ticket's message, and it writes to the field ticket_category.
- **rejoin_and_text**: The branches rejoin at a merge, and the step after it texts the record's owner "Triage finished".
- **models_and_blanks**: Every AI step uses kizen/lite, and the decision and the classification default a blank merge field to "Unknown".
