---
id: providers/engine
page: providers
section: engines
group: core
order: 1
after: [drawer/start]
point_at: "[data-section=engines] > button"
done_on: providers-changed
since: "1"
---
# Engines: where the words come from

Nothing answers without one. An engine is any server that answers in the OpenAI chat shape: a model you run yourself, a gateway, or a hosted service.

To add one, tap **+ Add** and fill in three things:

- **provider name**: whatever you call it
- **provider url**: its address, such as `http://localhost:11434/v1`
- **bearer token**: its key, if it needs one

One engine can answer for many wefts. Which one answers for a weft is on that weft's page.
