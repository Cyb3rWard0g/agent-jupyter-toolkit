# Data Query Guide

Use this guide when retrieving data from databases, data lakes, or log platforms.

## Query Principles

Data retrieval should maximize analytical visibility while avoiding unnecessary data loss.

### 1. Avoid restrictive queries

Do NOT immediately apply narrow filters.

Restrictive filters can hide important signals.

Instead:
- Start broad
- Narrow progressively

### 2. Avoid arbitrary row limits

Small limits (e.g., LIMIT 50) distort data distributions.

Preferred approaches:
- remove limits entirely
- use large limits when necessary
- retrieve sufficient data for meaningful exploration

### 3. Separate retrieval from analysis

Queries should retrieve relevant data.

Analysis should occur in the notebook environment using DataFrames.

SQL is best for:
- selecting fields
- filtering basic ranges
- joining tables

DataFrames are best for:
- aggregation
- grouping
- transformation
- statistical analysis

### 4. Inspect schema before querying

Before writing complex queries:

- inspect tables
- review column names
- understand data types
- identify time fields

Understanding schema prevents incorrect assumptions.

### 5. Validate retrieved data

After retrieving data:

- inspect sample rows
- verify column meanings
- check timestamp ranges
- confirm expected distributions

This step prevents misinterpretation of the dataset.