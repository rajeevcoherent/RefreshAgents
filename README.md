# refresh_agent: AI analyst fleet for market-research Input Sheets

A fleet of LLM analysts (DeepSeek, orchestrated with LangGraph) that builds the **Input Sheet** for a
market-estimate (ME) workbook. The Input Sheet holds base-year shares, CAGRs, country splits, segment
splits and ASPs, in the same layout analysts use, so the ME engine can consume it directly. **Every
number comes with its reasoning, its full calculation and its assumptions.**

The configured market is the *Global Industrial High Speed Helical Gearbox Aftermarket*, with base
year 2025 and a forecast to 2032. The market, its taxonomy and its definition documents are inputs, so
the same fleet can be pointed at another market.

Ground rules:
- **The LLM decides; Python computes.** Analysts commit to judgements (installed base, spend per
  unit, shares, growth). Code does all the arithmetic and logs every adjustment.
- **Every cell is explained.** On both the Data and ASP sheets, each value has a plain-English
  explanation, a step-by-step calculation ("725 = …"), the assumptions behind it, and what 2A, 2B
  and 2C each contributed.
- **The scope follows the client's definition documents.** What counts and what doesn't is taken
  verbatim from the definitions docx. The scope analyst may only draw boundaries; it may not invent
  numbers.
- **Generation is blind.** The publisher's own report is blocked from the evidence. Past human
  sheets teach *how* analysts form opinions (habits, spreads, typical shapes), never target values.

**Final deliverable:** [`final/Input Sheet - Global Industrial High Speed Helical Gearbox Aftermarket Market (prior-validated).xlsx`](final/)
(run `run4-scope`, prior-validated). Start with the *2A-2B-2C Definitions* sheet. Hover over any Data or
ASP cell for its explanation, calculation and assumptions, or filter the *Cell Reasoning* sheet.

---

## Quick start

```bash
pip install -r requirements.txt          # Python 3.12
cp .env.example .env                     # then add your DeepSeek API key

python run_input_sheet.py                # full run -> outputs/<run_id>/Input Sheet - <market>.xlsx
python validate_with_priors.py outputs/<run_id>   # cross-check against learned priors -> "(prior-validated).xlsx"
```

Useful flags for `run_input_sheet.py`:

| Flag | Effect |
|---|---|
| `--no-web` | Skip web research. Useful offline or for a quick run. |
| `--passes 1` | Draft only, with no reviewer redo. |
| `--no-opinions` | Don't give the analysts the learned house judgement. |
| `--allow-publisher` | Allow the publisher's own report as evidence (normally blocked). |
| `--run-id NAME` | Name the output folder. |

To regenerate a workbook from saved state without new LLM calls (except the plain-language editor):

```bash
python rebuild_workbook.py outputs/<run_id> [state.json | state_priorval.json]
```

### Inputs you must supply (not in this repo; client material)

| Input | Where it goes | Used for |
|---|---|---|
| Market definitions docx (aftermarket, plus OEM scope) | project root. The filenames are set in `analyst_fleet/config.py`. | What is in and out of scope; allocation rules; segment definitions. |
| OEM market workbooks (`*Gearbox OEM*.xlsx` / `*OEM Market.xlsx`) | any sub-folder of the root (auto-detected), or `FLEET_OEM_DIR` | Installed base, application mix and new-unit prices. |
| Past ME / Input Sheet folders (`April/`, `May/`, `July/`, …) | project root | Only needed to re-learn the corpus (`learn_from_corpus.py`, `learn_opinions.py`). The learned results are already in `data/corpus/`. |

All LLM calls, searches and pages are cached in `data/cache.sqlite` (git-ignored), so a rerun with
the same evidence is free and reproducible.

---

## How it works

The LangGraph pipeline is in `analyst_fleet/graph.py`:

```
scope -> research -> sizing -> regional -> countries -> segments -> pricing -> compute
      -> review (score + targeted redo) -> finalize -> write workbook
```

| Agent | Decides | Code then |
|---|---|---|
| Scope analyst | What's in and out, from the definitions docx (boundaries only; no numbers) | Rejects any invented numeric anchor |
| Research lead + extractor | 26–32 web queries and the numeric facts in the results | Keeps a fact only if its quote appears verbatim in the source; blocks the publisher |
| Market-sizing analyst | Installed base, spend per gearbox (5 in-scope components, each a % of the new-gearbox price), reading of cited figures, global CAGR, triangulation weights | Computes the bottom-up, top-down and cited routes and blends them into the global anchor |
| Regional and country analysts | Shares and CAGRs, with drivers, restraints and geopolitics | Normalises; reconciles child CAGRs to the parent, level by level |
| Segment analysts (country group × dimension) | Application and vertical shares per country, growth premiums | Normalises; segments add back to the country |
| Pricing analyst | Country price index and price trend | Price × volume reconciles with value |
| Reviewer (research director) | Rubric scores, rulebook compliance, targeted redo instructions | Reruns only the analysts it targets; keeps the best pass |

### 2A / 2B / 2C

These are the three estimation strategies. They are declared per market in
`analyst_fleet/taxonomy.json` under `strategies`.

| | Stands for | In this market |
|---|---|---|
| **2A** | Country totals, then top-down | Global size, region and country splits, growth |
| **2B** | Use case × country | By Industry Vertical |
| **2C** | Product type × application × country | By Application |

The first sheet of every Input Sheet ("2A-2B-2C Definitions") explains them for that market.

### Reasoning for every number

- **`analyst_fleet/calc_chain.py`** writes, for each cell, the full calculation from the first
  assumption to the cell value. Each step is tagged [2A], [2B] or [2C], and the assumptions are listed.
- **`analyst_fleet/cell_reasoning.py`** maps every Data and ASP cell and holds its explanation,
  scope basis and the research methodology.
- **`analyst_fleet/plain_language.py`** turns this into 3–5 plain sentences per cell: what the number
  is, how it was calculated, why, and geopolitics only when it is material. Code checks that every
  number in the text appears in that cell's facts; cells that fail fall back to structured text.
- **`analyst_fleet/writer.py`** puts it in a comment on every cell, in the **Cell Reasoning** sheet
  (one row per cell: explanation, calculation, assumptions, 2A/2B/2C contribution, evidence,
  confidence, scope basis) and in the **Research Methodology** sheet.

### Learning from past human sheets (house judgement, not targets)

```bash
python learn_from_corpus.py   # parse past MEs -> scale-free features, market classes, rules checked on held-out markets
python learn_opinions.py      # reconstruct how analysts formed opinions -> 2A/2B/2C heuristics (opinion_playbook)
python mimic_test.py          # held-out test: analyst with vs without the playbook
```

The learned results are committed in `data/corpus/`:

- `opinion_playbook.json` and `.md` are used by the fleet at run time.
- `library.json` and `rules.json` are used by `validate_with_priors.py`.
- `eval.json`, `stats_train.json`, `playbook.md` and `mimic_test.md` are the evaluation reports.

Two calibrations came out of the mimic test:

- Heuristics that made results worse on held-out markets were disabled. This applies to the
  region-share heuristics.
- Calibration notes take precedence over the general heuristics.

`validate_with_priors.py` needs no human sheet. It classifies the market, retrieves similar past
markets, and flags any feature outside the analog band (p10–p90 widened by 25%, which gives 81%
coverage on held-out markets). A validation lead then decides whether to keep the agent's number or
move it towards the prior. The reasoning is written into that cell. Priors never set the absolute
market size.

### Internal audit tools (outside the Input Sheet)

`cell_audit.py`, `compare_with_human.py` and `validate_against_human.py` compare a finished run with
a human ME when one exists. They write separate audit files. Human comparisons never appear inside
the Input Sheet.

---

## Output: `outputs/<run_id>/`

`Input Sheet - <market>.xlsx` contains these sheets:

| Sheet | Contents |
|---|---|
| 2A-2B-2C Definitions | What 2A, 2B and 2C stand for in this market |
| Data | Shares, CAGRs and the sizing block (region, share, CAGR, value) |
| ASP | Prices |
| Cell Reasoning | Per-cell explanation, calculation, assumptions and 2A/2B/2C contribution |
| Research Methodology | The overall approach |
| Sizing | Bottom-up, top-down and cited routes, and the in-scope spend build-up |
| Geography / Segment / Country Segment / Pricing Reasoning | The analysts' reasoning |
| Market Value | Market values |
| Validation | Validation results |
| Evidence, Facts, Checks & Review, Run Info | Internal tabs |

Every run also writes `state.json` (the full state), `summary.md` and `run.log`.

## Logging: `system.log`

The log is appended at the project root. It has one line per batch operation and per restriction,
in the form `time | run_id | event | key=value …`.

| Event | Meaning |
|---|---|
| `run.start` / `run.end` / `run.failed` | Run configuration, totals, or the crash reason |
| `batch.*`, `search.*`, `page.read` | Search plan, queries, pages read, extraction batches |
| `llm.call` / `llm.error` / `llm.failed` / `llm.fatal` | Every LLM call; network retries; hard stops (no balance or bad key) |
| `restriction.blocked` | A publisher source was blocked |
| `restriction.fact_dropped` | A fact failed the verbatim-quote rule |
| `restriction.reject` / `restriction.repair` | An analyst reply was rejected by a check; repaired JSON |
| `restriction.adjust` / `restriction.flag` | Code adjustments (normalisation, CAGR reconciliation) and check flags |
| `restriction.plain_fallback`, `plain.done` | Cells where the plain-language editor fell back to structured text |
| `corpus.*`, `restriction.rule`, `validate.*` | Corpus learning and prior validation |

## Repository layout

```
analyst_fleet/
  graph.py, config.py, llm.py, syslog.py     pipeline, settings, DeepSeek client (cache, retries, checks)
  agents/        scope+research, sizing, geo, segments, pricing, review, shared helpers
  evidence/      definitions docx -> scope rulebook; OEM workbooks; web search
  corpus/        ME parser, features, priors, opinion learning
  compute.py     all arithmetic
  calc_chain.py, cell_reasoning.py, plain_language.py, writer.py   per-cell reasoning and workbook
  validator.py, prior_validator.py           validation leads
  taxonomy.json  geographies, segment dimensions, 2A/2B/2C roles
final/           the final Input Sheet (prior-validated)
data/corpus/     learned playbook, priors library, rules, evaluation reports
run_input_sheet.py, validate_with_priors.py, rebuild_workbook.py      main entry points
learn_from_corpus.py, learn_opinions.py, mimic_test.py               corpus learning
cell_audit.py, compare_with_human.py, validate_against_human.py      internal audits
system.md        original system design document (the full 2A/2B/2C design and roadmap)
```

## Known limits and open items

**The ME engine**
- The engine reads the segment band from columns C–AD (28 geographies). This market has 46 leaf
  geographies.
- The engine's geography tree has 2 levels. This market has 3 (MEA → MENA → country).

**Not produced**
- Band 3 (trend-curve tags) is not produced, because it needs the publisher's internal trend library.

**Estimates to challenge first**
- The global size rests on annual spend per gearbox of about 7–11% of the new-gearbox price.
- The parts and overhaul components may overlap, which would push the size up. This should be
  reviewed.
- The Application split within countries is the least certain block.

**Not yet built**
- Truly separate 2A, 2B and 2C estimates, each triangulated against the others.
