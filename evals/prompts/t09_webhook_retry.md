# t09_webhook_retry: Webhook trigger, a counter variable, and a go-to retry

Tier 2 · custom object

Covers triggers: webhook  
Covers steps: initialize_variable, delay, math_operator, condition, stop_execution, update_variable, go_to_automation_step

## Prompt

Create an automation on the `eval_accounts` object named "{p} Webhook Retry" with api_name `{p}_webhook_retry`.

It is started by a webhook named `{p}_retry` that accepts any request (no authentication).

It uses two automation variables: a number variable `attempts` and a string variable `last_status`.

Steps, in order:

1. Wait 1 hour.
2. With a math step, add 1 to `attempts` and store the result back in `attempts`.
3. Check whether the variable `attempts` is 3 or more.
   - If yes: stop the run and mark it as Failed.
   - If no: set the variable `last_status` to the text "retrying", then jump back to the 1-hour wait in step 1.

## Rubric

- **webhook**: The webhook trigger is named with the run's prefix followed by "_retry" and requires no authentication.
- **variables**: There is a number variable attempts and a text variable last_status.
- **math**: The math step adds 1 to attempts and stores the result in attempts.
- **condition**: The condition tests the variable attempts for 3 or more.
- **yes_fails**: The yes branch stops the run and marks it Failed.
- **no_loops_back**: The no branch sets last_status to "retrying" and then jumps back to the 1-hour wait.
