# answer-engine-visibility

Does your brand get named when buyers ask AI?

This tool runs real buyer-intent questions through **grounded** AI answer engines
(Claude with web search, Gemini with Google Search grounding, Tavily cited search),
detects whether a target brand is named or cited versus its competitors, adds a
technical crawlability score, and renders a one-page HTML report.

## Why grounded matters

Calling a model *without* web search measures what its training data remembers,
which under-reports every niche brand. In testing, the same brand measured 21%
presence ungrounded vs **71% grounded**, and another 29% vs **88%**. Buyers use
assistants that search, so the probe searches. Every number in the report comes
from a live retrieval run; nothing is estimated.

## What you get

- **Answer-engine presence**: the share of buyer questions where the brand was
  named or cited, with a per-question, per-engine grid.
- **Who got cited instead**: a ranked tally of the competitors taking the answers.
- **Where the citations leak**: the exact questions the brand loses, per engine,
  and who wins them, so the fix list is specific, not generic.
- **Technical crawlability (0-100)**: AI-crawler access in robots.txt, llms.txt,
  JSON-LD structured data, sitemap, and on-page basics, so you know whether the
  gap is the site or the citations.
- A raw audit JSON backing every cell, so the score can be verified.

## Run it

```bash
export ANTHROPIC_API_KEY=...   # Claude web search
export GEMINI_API_KEY=...      # Gemini grounding
export TAVILY_API_KEY=...      # Tavily cited search

python probe.py <brand-slug>
```

Add a brand by adding an entry to `CONFIGS` in `probe.py`:

```python
"acme": {
    "brand": "Acme Robotics",
    "domain": "acmerobotics.com",
    # word-boundary safe; for common-word brands, require the full name or the
    # word near a category term (see the existing entries for patterns)
    "brand_regex": r"\bacme\s+robotics\b|acmerobotics\.com",
    "category": "warehouse automation robots",
    "competitors": ["Locus", "6 River", "Fetch", "GreyOrange"],
    "prompts": [
        "What are the best warehouse automation robots?",
        # ... 8 real buyer questions, written the way a buyer types them
    ],
},
```

Output: `<domain-stem>_report_<date>.html` + `<domain-stem>_raw_audit.json`
beside the script. Reports are dated and never overwritten.

Standard-library HTTP only; no dependencies.

## Honesty rules baked in

- Engines are grounded only; an ungrounded refactor would silently falsify results.
- The report only states what the probes returned, with the raw audit as receipts.
- Detection uses word-boundary regexes with an audit flag for near-misses, so
  common-word brand names (an "eve", a "thrive") do not inflate or deflate scores.

MIT. Built by [Dallin Rowley](https://dallinrowley.vercel.app).
