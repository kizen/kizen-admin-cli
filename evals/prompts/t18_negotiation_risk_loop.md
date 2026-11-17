# t18_negotiation_risk_loop: Global automation that loops over a search and edits each record

Tier 3 · global

Covers triggers: manual  
Covers steps: initialize_variable, search_records, loop, next_or_finish, modify_related_entities

## Prompt

Create a global automation (one with no target object) named "{p} Negotiation Risk" with api_name `{p}_negotiation_risk`. It is started manually.

Each run searches the `eval_deals` pipeline for every deal whose stage is "Negotiation", then loops over the deals found. Inside the loop, it sets the `risk` field of the deal the loop is on to "High".

## Rubric

- **search**: The search looks in eval_deals for records whose stage is "Negotiation".
- **loop_sets_risk**: The loop runs over the search result, and inside it the field risk is set to "High" on the deal the loop is on.
