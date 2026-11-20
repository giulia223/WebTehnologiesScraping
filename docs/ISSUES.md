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
##  bot check only for the headless browser, plain HTTP gets the page
- Date found: 20.11
- Found by: Claude (blind inspection, task 0.3)
- Where: 1stlocksheathscouts.org.uk
- What happens: Cloudflare shows the headless Chrome a "Verify you are human" check (HTTP 403, Turnstile), while a plain HTTP request (curl with a browser user-agent) gets the real page. So everything that only appears after JavaScript runs (analytics, embeds, JS globals, cookies set by scripts) cannot be seen; only the page source, headers and robots.txt can. In our own crawl it was the reverse (plain HTTP got the check, the browser got the page): which client is blocked varies between visits.
- Status: open (accepted for now: we do not try to get past protections)
- How I'd tackle it (now, and at scale): now, report which signals could not be checked on such sites (the static page still counts) and keep the static fetch as a fallback. At scale, measure how many sites block only the browser, and decide per site which tier to use instead of always running the browser.
```

tested by hand:
- jcmobilecigars.com, 11853prospecthill.com, wglchurch.com, nwmotorinn.com, verticalcommunitychurch.com, tamcconstruction.net -> name doesn t resolve
- 1ststopsupply.com  aardvarkelectriccorp.com, 1stclairavewest.com -> error 1001 and nesecurizate
- gogroupauto.com howellmotors.com - worked but with site doesn c accept a secured sonexion.  huberautomotive.com - worked, concrete-fitness.edan.io - cloudfare check
- stwanna - Sorry, you have been blocked
You are unable to access dealercenterwebsite.net
- fisherinvestments.com - 403
- aida-cruises.at,  ecolab.com roccofortehotels.cn  brucebournesold.com -> worked
- everhartsoutdoor.com site doesn c accept a secured sonexion.
- juanmunozhomesmart.com - Sorry for the inconvenience, this site is currently under construction... is this your website?
- chavchavadze-m2-41-tbilisi.booked.net - 404
-  2ldm.com - Sucuri Website Firewall - Not Configured
-  eclairage-solaire-occitanie.com  - domain not connected to a site
-  startravelcompany.com. 400
- branson-connection.com redirect with claoude not robot and human verification with pressing keys
- dioxoxigenio.com.br works
- den-a-sha.co.jp  works
-  miratrade.com.tr coming soon
- maiquotes.net conexion not secure
- 
---
