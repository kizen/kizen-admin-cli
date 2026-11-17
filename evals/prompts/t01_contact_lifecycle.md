# t01_contact_lifecycle: Set a field when a contact is created

Tier 1 · contacts

Covers triggers: new_entity_created  
Covers steps: change_field_value

## Prompt

Create an automation on Contacts named "{p} Contact Lifecycle" with api_name `{p}_contact_lifecycle`.

When a new contact is created (on creation only, not on later updates), set the contact's `eval_lifecycle` field to "New".

## Rubric

- **fires_on_create_only**: The trigger fires when a contact is created, and not on later updates.
- **sets_lifecycle_new**: The one action step sets the Contacts field eval_lifecycle to the option "New".
