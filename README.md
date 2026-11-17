# techdetect

Finds the technologies used by a list of websites, with evidence for every detection. (Work in progress.)

## Known limitations

- Requests made by dedicated Web Workers (e.g. Shopify app pixels) are captured, but recorded with the origin of the frame that created the worker, usually `main`. Playwright does not expose which worker sent a request. The workers seen on each page are listed in `browser.json`.
