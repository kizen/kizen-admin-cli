# Create a Contact for an email no Contact has yet; the output links it.
email = (inputs.customer_email or "").strip()
if not email:
    raise Exception("customer_email is blank: no Contact to create")
resp = kizen.api.post(
    "/records/client_client/add", json={"fields": [{"name": "email", "value": email}]}
)
if resp.status_code != 201:
    raise Exception(
        f"creating a Contact for {email} failed: {resp.status_code} {resp.text[:200]}"
    )
outputs.customer = resp.json()["id"]
