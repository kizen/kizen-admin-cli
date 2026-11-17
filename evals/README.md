# Agent-usability evals: prompts and rubrics

Thirty automation-building tasks for testing how well an agent that has never
seen `kizen` can use it, and what it costs the agent to get there. Each task is
a prompt to hand to a fresh session and a rubric to grade the result against.

This folder is prompts, rubrics and the data they refer to. There is no
harness: an agent starts the sessions and grades them.

**None of this has been run against a live business yet.** The prompts were
written from the shipped docs, and each one had a reference spec that the
CLI's planner accepted offline against a fake of the world below. Expect to
fix some prompts, rubric lines and world details on the first live pass.

## Running one task

1. Start a session with no skills, memory or prior notes, in a folder set up
   by `kizen init` for a disposable business that holds the eval world (below).
2. Pick a short unique prefix for the session, such as `ev7k2q`, and replace
   every `{p}` in the prompt, and in any setup spec, with it. The prefix
   keeps sessions apart and makes cleanup a matter of deleting what carries it.
3. Do what the task's "Before the session" section says, if it has one.
4. Give the session [`preamble.md`](preamble.md) followed by the task's prompt.
   The preamble grants approval to apply, which the operating docs otherwise
   tell an agent to wait for.
5. When it stops, read back each automation the task asked for
   (`kizen automations get <api_name> --raw`) and grade it.
6. Delete every automation whose api_name starts with the prefix.

## Grading

Grade the automations the session left in Kizen, not what the session said
about them. Each rubric line is one yes/no fact. Give it pass, fail or
can't-tell, with the value that decided it. A build is correct when every line
passes, counting the task's own rubric and these, which apply to every task:

- **named_as_asked**: each automation has the name and api_name the task
  gives, on the object the task names (or no object, for a global automation).
- **nothing_extra**: no trigger or step the task doesn't describe. A manual
  trigger is always present and doesn't count, nor do variable-declaring steps
  a described step needs, nor the closing steps of loops and merged branches.
- **inactive_unless_asked**: each automation is inactive, unless the task says
  to activate it.

Where a rubric line asks for a merge field "that resolves", the namespace has
to suit the step: `entity_record.<field>` in text and email steps,
`custom_objects.<field>` in AI prompts, activity notes and variable sources,
`automation_variable.<name>` for a variable (`kizen docs show automation`,
"Merge-field namespace tokens by step type"). The prompts never spell a merge
field, on purpose.

Nothing here runs an automation, so "correct" means configured as asked.

Separately from correctness, these are worth a yes/no per session:

- It ran a dry run before the first apply.
- It changed nothing outside its prefix.
- It reached Kizen only through the CLI: no direct HTTP, no reading the
  installed package's source.
- It never deleted and recreated an automation to get past an error.
- Its final message matches what it actually did.

## What to count

The goals are that sessions get more reliable and cheaper as the CLI and its
docs change. Per session, from the transcript:

- correct or not
- tool calls, and `kizen` calls (one tool call can chain several)
- `kizen` calls that failed, split by kind: rejected command line (no such
  command or option, missing argument), `spec error:`, `plan error:`,
  HTTP 4xx/5xx, anything else
- `--help` calls, and `kizen docs` calls with the topics opened
- dry runs before the first successful apply
- tokens or cost, and wall time
- where the CLI, its help or its docs cost the session calls it shouldn't have
  needed, in a sentence each

Sessions vary. Run a task a few times, and run the same CLI twice before
comparing two versions, to see how far identical runs differ.

## The eval world

[`world/world.json`](world/world.json) lists what the business must hold. Every
prompt names things from it:

- `eval_accounts` and `eval_tickets` (standard objects) and `eval_deals` (a
  pipeline with five stages), with the fields listed
- five `eval_` fields on Contacts
- activity types "Eval Call" and "Eval Follow Up"
- the form "Eval Ticket Intake" and the survey "Eval Contact Survey"
- the email template "Eval Notice"

Objects are in dependency order. Kizen derives an object's api_name from its
display name, so the names must be created exactly as written. The `<object>`
and `<object.field>` tokens in the activity specs stand for UUIDs to fill in
from `kizen objects get <object> --json`.

## Tasks

| Task | Tier | Surface | What it exercises |
|---|---|---|---|
| [t01_contact_lifecycle](prompts/t01_contact_lifecycle.md) | 1 | contacts | Set a field when a contact is created |
| [t02_won_deal_text](prompts/t02_won_deal_text.md) | 1 | pipeline | Text the owner when a deal is won |
| [t03_account_tier_assign](prompts/t03_account_tier_assign.md) | 1 | custom object | Reassign an account a day after its tier changes |
| [t04_ticket_intake_followup](prompts/t04_ticket_intake_followup.md) | 1 | custom object | Schedule a follow-up when a form is submitted |
| [t05_lost_deal_archive](prompts/t05_lost_deal_archive.md) | 1 | pipeline | Archive a lost deal after 30 days |
| [t06_call_outcome_branch](prompts/t06_call_outcome_branch.md) | 2 | pipeline | Branch on what a logged call recorded |
| [t07_closing_soon_gate](prompts/t07_closing_soon_gate.md) | 2 | pipeline | Date trigger with a gate that cancels small deals |
| [t08_survey_score](prompts/t08_survey_score.md) | 2 | contacts | Survey trigger with a numeric condition |
| [t09_webhook_retry](prompts/t09_webhook_retry.md) | 2 | custom object | Webhook trigger, a counter variable, and a go-to retry |
| [t10_overdue_goal](prompts/t10_overdue_goal.md) | 2 | custom object | Wait for an event with a goal step |
| [t11_weekly_account_sweep](prompts/t11_weekly_account_sweep.md) | 2 | global | Scheduled global automation that starts another on each match |
| [t12_ticket_find_contact](prompts/t12_ticket_find_contact.md) | 3 | custom object | Search, loop over the result, and fall back to a code step |
| [t13_ticket_triage_ai](prompts/t13_ticket_triage_ai.md) | 3 | custom object | File extraction, an AI decision, and branches that rejoin |
| [t14_contact_reply_summary](prompts/t14_contact_reply_summary.md) | 3 | contacts | Email trigger, a code step into a variable, and an AI summary |
| [t15_contact_voicemail](prompts/t15_contact_voicemail.md) | 3 | contacts | Transcribe an audio file with a model the environment supports |
| [t16_deal_won_onboarding](prompts/t16_deal_won_onboarding.md) | 3 | pipeline | Parallel lanes that create and modify related records, then rejoin |
| [t17_renewal_emails](prompts/t17_renewal_emails.md) | 3 | custom object | Two email steps, each needing its own message resource |
| [t18_negotiation_risk_loop](prompts/t18_negotiation_risk_loop.md) | 3 | global | Global automation that loops over a search and edits each record |
| [t19_ticket_kb_draft](prompts/t19_ticket_kb_draft.md) | 3 | custom object | AI step that calls an MCP server's tools |
| [t20_edit_existing](prompts/t20_edit_existing.md) | 2 | contacts | Edit and insert steps in an existing automation |
| [t21_remove_and_activate](prompts/t21_remove_and_activate.md) | 2 | contacts | Remove a step from an existing automation and activate it |
| [t22_enterprise_upgrade](prompts/t22_enterprise_upgrade.md) | 2 | custom object | Field trigger on one specific value, and an owner copied from a field |
| [t23_stalled_proposal](prompts/t23_stalled_proposal.md) | 2 | pipeline | Stage-to-stage trigger, today's date, a stage check, a stage move |
| [t24_birthday_greeting](prompts/t24_birthday_greeting.md) | 1 | contacts | Yearly date trigger on a Contact |
| [t25_secured_webhook](prompts/t25_secured_webhook.md) | 3 | custom object | Header-authenticated webhook, and a code step with a fixed input and a variable output |
| [t26_deal_account_brief](prompts/t26_deal_account_brief.md) | 3 | pipeline | A related record's field in a variable, and an AI step with two destinations |
| [t27_contract_extraction](prompts/t27_contract_extraction.md) | 3 | pipeline | File extraction that reads a related record's file |
| [t28_tier_sweep](prompts/t28_tier_sweep.md) | 3 | global | A list variable drives a search, and another list is appended to |
| [t29_add_branch_to_existing](prompts/t29_add_branch_to_existing.md) | 2 | contacts | Edit a condition and add a step on its other branch |
| [t30_goal_stage_wait](prompts/t30_goal_stage_wait.md) | 2 | pipeline | A goal with no time limit, and an activity due relative to a date field |

Tier 1 is one trigger and a step or two. Tier 2 adds branching, variables,
waits, a go-to, a second automation, or editing an existing automation. Tier 3
adds loops, code steps, AI steps, parallel lanes, related records and
multi-command builds.

Together the tasks cover all twelve wired triggers and every wired step type
but two: `plugin_code_step`, which needs an installed plugin, and
`send_related_contact_text`, which points at a text message resource the CLI
has no command to create. Most types appear in two or more tasks, in different
configurations.

## Known wrinkles

- `t22`: the automation doc says a `field_updated` trigger's `to_value` is
  written as a bare UUID. The spec model refuses that and wants
  `{"id": "<uuid>"}`. A session following the doc will hit a spec error.
- `t11`, `t12`, `t14` and `t19` sit close to worked examples in the automation
  doc, so they measure finding the example as much as building from scratch.
  They will move most if those examples move.
- `t15` and `t19` depend on the business: a transcription model, and the
  `readonly` MCP server with its service account.
- `t20`, `t21` and `t29` ask for an in-place edit. Their rubrics compare step
  ids with the automation as it was set up, so read it back before the session.

## Layout

```
evals/
  preamble.md     the text every session gets ahead of its prompt
  prompts/        one page per task: prompt and rubric
  setup/          automations some tasks need to exist beforehand
  assets/         scripts some prompts hand to the session
  world/          what the test business must contain
```
