# Data Analysis Techniques

Use these techniques when exploring and investigating structured datasets.

## 1. Row counting

Start with simple row counts.

Example goals:
- total events
- events per entity
- events per time window

This helps understand scale.

---

## 2. Frequency analysis

Count occurrences of categorical values.

Examples:
- logon types
- event actions
- usernames
- process names

Frequency distributions reveal dominant behaviors.

---

## 3. Grouping and aggregation

Group events by meaningful dimensions.

Examples:
- events per device
- events per IP address
- events per user

Aggregation surfaces abnormal concentrations.

---

## 4. Time-based analysis

Analyze temporal patterns.

Examples:
- activity bursts
- unusual login hours
- sudden spikes in events

Time analysis often reveals attack sequences.

---

## 5. Outlier detection

Identify entities behaving differently from the baseline.

Examples:
- users with excessive failures
- IPs generating abnormal traffic
- processes appearing rarely

Outliers frequently indicate suspicious behavior.

---

## 6. Correlation analysis

Compare attributes across events.

Examples:
- user ↔ IP
- process ↔ host
- login ↔ process execution

Correlation helps reconstruct activity chains.