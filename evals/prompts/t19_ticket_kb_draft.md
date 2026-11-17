# t19_ticket_kb_draft: AI step that calls an MCP server's tools

Tier 3 · custom object

Covers triggers: manual  
Covers steps: initialize_variable, call_llm, change_field_value

## Before the session

- The business needs the `readonly` MCP server and a service account named "Agentic Workflow Code".

## Prompt

Create an automation on the `eval_tickets` object named "{p} KB Draft" with api_name `{p}_kb_draft`. It is started manually. It uses one string variable, `draft_answer`.

Steps, in order:

1. An AI step using the `kizen/pro` model that can call the tools of the MCP server `readonly`, running those tools as the service account named "Agentic Workflow Code". Its prompt is "Search the knowledge catalog for this support request, then draft a short answer. Request subject: " followed by the ticket's subject, filled in from the record.
   A blank value should default to "Unknown". It writes its answer to the variable `draft_answer`.
2. Set the ticket's `summary` field to the value of the variable `draft_answer`.

## Rubric

- **ai_step**: The AI step uses kizen/pro, with the MCP server readonly and the service account "Agentic Workflow Code".
- **prompt**: Its prompt asks to search the knowledge catalog and draft a short answer, and includes a merge field that resolves to the ticket's subject. A blank value defaults to "Unknown".
- **writes_variable**: It writes to the variable draft_answer.
- **saves_summary**: The last step sets the field summary from the variable draft_answer, not from fixed text.
