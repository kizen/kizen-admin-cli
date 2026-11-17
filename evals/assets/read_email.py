# Read the newest email this Contact sent to a connected inbox, as plain text.
import html
import re


def ok(resp, what):
    if resp.status_code != 200:
        raise Exception(f"{what} failed: {resp.status_code} {resp.text[:200]}")
    return resp.json()


email = (inputs.contact_email or "").strip()
if not email:
    raise Exception("the Contact has no email to look it up by")
match = {"type": "fields", "subtype": "non_custom", "field": "email"}
query = [{"and": True, "filters": [{**match, "condition": "=", "value": email}]}]
found = ok(
    kizen.api.post(
        "/records/client_client/search",
        json={"query": query, "and": True, "field_names": ["email"]},
    ),
    "searching Contacts",
)["results"]
if len(found) != 1:
    raise Exception(f"{len(found)} Contacts have email {email}, not 1")

events = ok(
    kizen.api.get(
        f"/records/client_client/{found[0]['id']}/timeline", params={"size": 50}
    ),
    "reading the timeline",
)["events"]
inbound = [e for e in events if e["type_name"] == "integrated_inbox_email_received"]
outputs.email_body = ""
if not inbound:
    outputs.log(f"no inbound email in the Contact's last {len(events)} timeline events")
else:
    data = max(inbound, key=lambda e: e["created"])["data"]
    sid = data.get("synced_email_id") or data.get("syncedEmailId")
    if not sid:
        raise Exception(f"no synced email id in the event's data: {sorted(data)}")
    body = ok(kizen.api.get(f"/synced-email/{sid}/full_response"), f"reading {sid}")
    text = body.get("text_plain") or ""
    if not text.strip():
        markup = body.get("text_html") or ""
        markup = re.sub(r"(?is)<(script|style).*?</\1>", "", markup)
        markup = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</li>", "\n", markup)
        text = html.unescape(re.sub(r"<[^>]+>", "", markup))
    outputs.email_body = text.strip()
    outputs.log(f"read email {sid}: {len(outputs.email_body)} characters")
