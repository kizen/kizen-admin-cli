# t27_contract_extraction: File extraction that reads a related record's file

Tier 3 · pipeline

Covers triggers: manual  
Covers steps: file_content_extraction, change_field_value

## Prompt

Create an automation on the `eval_deals` pipeline named "{p} Contract Terms" with api_name `{p}_contract_terms`. It is started manually.

Steps, in order:

1. A file content extraction step using the `kizen/lite` model. The file it reads is not on the deal: it is the `contract` file field of the account the deal is related to through its `account` relationship field. Its prompt is "Summarize the key commercial terms of this contract in three bullet points." It writes the result to the deal's `ai_brief` field.
2. Set the deal's `next_step` field to "Review contract summary".

## Rubric

- **reads_related_file**: The extraction step reads the contract field of the account reached through the deal's account relationship field, not a field on the deal.
- **prompt_and_destination**: It uses kizen/lite with the prompt "Summarize the key commercial terms of this contract in three bullet points." and writes to the deal's ai_brief field.
- **next_step**: The step after it sets next_step to "Review contract summary".
