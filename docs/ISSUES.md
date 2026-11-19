# Issues log

Purpose: real problems found while building. This file feeds README debate question 1: *"What were the main issues with your current implementation, and how would you tackle them?"*



```
##  x
- Date found:
- Found by: me / Claude 
- Where: (file, domain, phase)
- What happens: (a concrete example)
- Status: open / fixed in task X.Y / accepted limitation
- How I'd tackle it (now, and at scale):
```


```
##  different language sites -> different subpages routes to ask for
- Date found:16.11
- Found by: me 
- Where: domains file
- What happens: domains from different countries, so different languages - possible pb later ->dk letters found in domain name
- Status: open
```
```
##  root domain
- Date found: 16.11
- Found by:  Claude
- Where: domains file
- What happens: some are not root domain, but platform subdomains(9). 
- Status: open 
- How I'd tackle it (now, and at scale): label those rows
```

```
##  language of sites - Acceptance Language
- Date found:
- Found by: me
- Where: inspecting clotting-guide.online
- What happens: i am asked to choose a language or its own
- Status: send request with en-us
- How I'd tackle it (now, and at scale):
```

```
##  DNS probe did not query SOA records
- Date found: 2026-11-17
- Found by: Claude
- Where: collect/dns_probe.py (DNS step), found while loading the webappanalyzer rules
- What happens: 51 webappanalyzer `dns` rules match the SOA record (the zone's primary name server and admin mailbox, e.g. a DNS host's name), but the probe only asked for A, AAAA, CNAME, MX, NS and TXT, so those rules could never match.
- Status: fixed in task 3.1 (SOA added to the probe; the exact input host is queried, so a subdomain without its own zone records no_answer)
- How I'd tackle it (now, and at scale): check the record types the loaded rules use against the types the probe asks for, so a rule update that adds a new type is noticed instead of silently never matching.
```
---
