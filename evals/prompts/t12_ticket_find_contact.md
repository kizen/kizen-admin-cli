# t12_ticket_find_contact: Search, loop over the result, and fall back to a code step

Tier 3 · custom object

Covers triggers: new_entity_created  
Covers steps: initialize_variable, search_records, loop, next_or_finish, change_field_value, condition, code_step

## Before the session

- Put `assets/create_contact.py` in the session's working folder.

## Prompt

Create an automation on the `eval_tickets` object named "{p} Find Contact" with api_name `{p}_find_contact`.

It runs when a new ticket is created (creation only), and links the ticket to the Contact whose email matches the ticket's `customer_email`, creating that Contact when there is none:

1. Search Contacts for records whose `email` equals the ticket's `customer_email`.
2. Loop over the contacts found. Inside the loop, set the ticket's `customer` relationship field to the contact the loop is on.
3. After the loop, check whether the ticket's `customer` field is still blank.
   - If it is blank: run a code step with the Python script in `create_contact.py` (in this folder), unchanged. The script reads one input named `customer_email`, bound to the ticket's `customer_email` field, and writes one output named `customer`, bound to the ticket's `customer` field.
   - If it is not blank: do nothing.

## Rubric

- **search**: The search looks in Contacts for records whose email equals the ticket's customer_email, compared through a variable that holds that field's value.
- **loop_links**: The loop runs over the search result, and inside it the field customer is set to the contact the loop is on.
- **blank_check**: After the loop, the condition tests whether the field customer is blank.
- **code_step**: The yes branch runs a code step whose script is the provided create_contact.py, unchanged, with the input customer_email bound to the field customer_email and the output customer bound to the field customer.
