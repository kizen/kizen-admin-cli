# Tag a ticket from its subject. `mode` is "strict" or "loose".
subject = (inputs.subject or "").lower()
words = ("vip", "priority") if inputs.mode == "loose" else ("vip",)
outputs.tag = "vip" if any(w in subject for w in words) else "standard"
