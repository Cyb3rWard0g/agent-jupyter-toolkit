---
name: data-exploration-analysis
description: Perform structured data exploration and analysis using notebook execution. Use this skill when working with datasets retrieved from databases, data lakes, or files and you need to understand their structure, investigate patterns, and produce defensible analytical findings. The objective is to progressively explore the data, validate assumptions, apply analysis techniques, and communicate findings through code, visualizations, and markdown explanations.
metadata:
  short-description: Structured data exploration and analysis workflow
---

# Data Exploration and Analysis

Use this skill to guide systematic analysis of structured datasets using notebook execution and DataFrame-based workflows. The goal is to move from raw data retrieval to validated insights while maintaining transparency, reproducibility, and analytical rigor.

## Workflow

- You MUST complete each step in order.
- You MUST NOT skip directly to conclusions or visualizations before understanding the data.
- Always prefer incremental exploration over overly complex queries.
- All reasoning and conclusions MUST be documented in markdown cells.
- Reference documents (under `references/`) MUST be read progressively — only when the current step calls for them. Do NOT read all reference documents upfront. Each step specifies which reference to consult; read it at that point and not before.

---

### Step 1: Understand the dataset structure

Before performing analysis, establish a basic understanding of the dataset.

- Identify available tables or data sources.
- Inspect schema and column definitions.
- Determine key attributes such as timestamps, identifiers, and categorical fields.
- Identify potential join keys or relationships if multiple tables exist.

Use guidance from `references/data-query-guide.md`.

This step is complete only when the structure and basic semantics of the data are understood.

---

### Step 2: Retrieve an exploratory dataset

Retrieve an initial dataset that allows you to observe the structure and distribution of the data.

- Start with broad queries rather than narrow filters.
- Avoid arbitrarily small limits that obscure patterns.
- Prefer retrieving data into a DataFrame for exploration.
- Ensure the dataset includes sufficient rows to capture variability.

Use guidance from `references/data-query-guide.md`.

Do NOT perform complex filtering or aggregation during this step.

---

### Step 3: Perform exploratory analysis

Explore the dataset to understand distributions, anomalies, and relationships.

- Inspect row counts and column distributions.
- Identify categorical values and frequency patterns.
- Examine time ranges and event densities.
- Identify missing or unexpected values.

Apply techniques from `references/data-analysis-techniques.md`.

This step is complete when the analyst understands the major characteristics of the dataset.

---

### Step 4: Investigate patterns and hypotheses

Once the data is understood, begin targeted analysis.

- Formulate hypotheses about potential patterns or anomalies.
- Filter and group data to test hypotheses.
- Compare behaviors across entities (users, hosts, IPs, processes, etc.).
- Identify statistical outliers or behavioral deviations.

Continue applying techniques from `references/data-analysis-techniques.md`.

Do NOT jump directly to conclusions without validating results.

---

### Step 5: Visualize findings

Use visualizations to clarify patterns and support analytical conclusions.

- Prefer simple visualizations that highlight structure or anomalies.
- Use charts to reveal trends, distributions, and correlations.
- Ensure visualizations accurately represent the underlying data.

Apply guidance from `references/data-visualization-guide.md`.

---

### Step 6: Document conclusions

Summarize the analytical findings.

- Explain the analytical steps performed.
- Describe the patterns observed in the data.
- Connect observations to the original investigation objective.
- Clearly state any assumptions or uncertainties.

The final output must include both code-based evidence and written explanation.