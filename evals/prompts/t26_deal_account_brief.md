# t26_deal_account_brief: A related record's field in a variable, and an AI step with two destinations

Tier 3 · pipeline

Covers triggers: manual  
Covers steps: initialize_variable, call_llm

## Prompt

Create an automation on the `eval_deals` pipeline named "{p} Account Brief" with api_name `{p}_account_brief`. It is started manually.

It uses one string variable, `account_industry`, which starts each run holding the `industry` of the account the deal is related to through its `account` relationship field.

It then has one AI step using the `kizen/lite` model. Its prompt is "Write a two-sentence brief on this deal.", followed by three labelled values filled in when the step runs: the deal's name, the value of the variable `account_industry`, and the deal's `call_notes`. A blank value should default to "Unknown".

The step writes its answer to two places: the deal's own `ai_brief` field, and the `health_notes` field of the account the deal is related to through `account`.

## Rubric

- **variable**: The variable account_industry is filled from the field industry of the account reached through the deal's account relationship field.
- **prompt**: The AI step uses kizen/lite. Its prompt begins "Write a two-sentence brief on this deal." and includes merge fields that resolve to the deal's name, the variable account_industry and the deal's call_notes. A blank value defaults to "Unknown".
- **two_destinations**: The AI step writes to the deal's ai_brief field and to the health_notes field of the account reached through account.
