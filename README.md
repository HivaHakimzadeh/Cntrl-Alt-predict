# Agentic Blast-Radius Guardrail

A real-time safety checkpoint for AI agents that take real-world actions.  
Runs entirely on **IBM LinuxONE (s390x)** — hybrid Rules + Local ML Classifier.

---

## The Problem

AI agents can now send emails, move money, delete files, and call APIs on their own. If one gets tricked (prompt injection) or just goes wrong, there's usually nothing in place to catch it before damage is done.

---

## The Solution

A guardrail layer that sits between an AI agent and the tools it's allowed to use. Every action the agent tries to take is scored for how much damage it could cause ("blast radius") before it's allowed to go through.

* **`ALLOW`**: Low-risk actions pass automatically
* **`QUARANTINE`**: Medium / ambiguous actions are held for human review
* **`BLOCK`**: High-risk or suspicious actions are refused

---

## How It Works (short version)

1. **Intercept** — Catch every tool call the agent tries to make.
2. **Score** — Rate the action's potential damage using:
   * **Fast deterministic rules** (irreversible? large amount? unknown destination? contradicts user request?)
   * **Local ML classifier** (`scikit-learn` TF-IDF + Logistic Regression trained on injection vs. benign samples) running on LinuxONE.
3. **Decide** — `ALLOW` / `QUARANTINE` / `BLOCK` and write a structured audit log.

> *See `BLUEPRINT.md` for the full architecture and demo flow.*

---

## Architecture

```text
Tool Call Request
       │
       ▼
┌──────────────────┐
│   Interceptor    │  ← single chokepoint
└────────┬─────────┘
         │
         ▼
┌──────────────────┐
│  Hybrid Scorer   │
│  • Rule engine   │  (fast, deterministic)
│  • Local ML      │  (scikit-learn on LinuxONE)
└────────┬─────────┘
         │
         ▼
┌──────────────────┐
│     Policy       │  → ALLOW / QUARANTINE / BLOCK
└────────┬─────────┘
         │
         ▼
   Structured Log + Action
