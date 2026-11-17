# t14_contact_reply_summary: Email trigger, a code step into a variable, and an AI summary

Tier 3 · contacts

Covers triggers: email_received  
Covers steps: initialize_variable, code_step, call_llm, change_field_value

## Before the session

- Put `assets/read_email.py` in the session's working folder.

## Prompt

Create an automation on Contacts named "{p} Reply Summary" with api_name `{p}_reply_summary`.

It runs when an email is received from the contact, in any connected inbox. It uses two string variables, `email_body` and `reply_summary`.

Steps, in order:

1. A code step running the Python script in `read_email.py` (in this folder), unchanged. It has one input named `contact_email`, bound to the contact's `email` field, and one output named `email_body`, which writes the variable `email_body`.
2. An AI step using the `kizen/lite` model whose prompt is "Summarize this customer's email reply in one sentence. Reply: " followed by the value of the variable `email_body`, filled in when the step runs.
   A blank value should default to "Unknown". It writes its answer to the variable `reply_summary`.
3. Set the contact's `eval_reply_summary` field to the value of the variable `reply_summary`.

## Rubric

- **trigger**: The trigger is an email received from the contact in any connected inbox.
- **code_step**: The code step's script is the provided read_email.py, unchanged, with the input contact_email bound to the field email and the output email_body writing the variable email_body.
- **ai_step**: The AI step uses kizen/lite, its prompt includes a merge field that resolves to the variable email_body, a blank value defaults to "Unknown", and it writes to the variable reply_summary.
- **saves_summary**: The last step sets the field eval_reply_summary from the variable reply_summary, not from fixed text.
