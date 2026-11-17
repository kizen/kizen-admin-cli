# t24_birthday_greeting: Yearly date trigger on a Contact

Tier 1 · contacts

Covers triggers: on_or_around_date  
Covers steps: notify_member_via_text

## Prompt

Create an automation on Contacts named "{p} Birthday Greeting" with api_name `{p}_birthday_greeting`.

It runs on each contact's `birthday`, on the day itself, at 8:00 am, and repeats every year. It sends a text message to the contact's owner saying "Birthday today: " followed by the contact's first name (filled in from the record).

## Rubric

- **trigger**: The trigger is the field birthday, on the day itself, at 8:00 in the morning, repeating every year.
- **text**: The step texts the record's owner "Birthday today: " followed by a merge field that resolves to the contact's first name.
