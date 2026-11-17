# t17_renewal_emails: Two email steps, each needing its own message resource

Tier 3 · custom object

Covers triggers: on_or_around_date  
Covers steps: notify_member_via_email, send_related_contact_email

## Prompt

Create an automation on the `eval_accounts` object named "{p} Renewal Emails" with api_name `{p}_renewal_emails`.

It runs 30 days before each account's `renewal_date`, at 8:00 am. Steps, in order:

1. Email the account's owner, using the email template named "Eval Notice".
2. Email the contact in the account's `primary_contact` relationship field, also using the "Eval Notice" template.

## Rubric

- **date_trigger**: The trigger is 30 days before the field renewal_date, at 8:00 in the morning.
- **owner_email**: The first step emails the record's owner with a message built from the template "Eval Notice".
- **contact_email**: The second step emails the contact in the field primary_contact with a message built from the template "Eval Notice".
