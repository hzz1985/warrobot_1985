"""Local-agent layer for the trading bot.

Design rules (agreed with the operator):
  * strategy is fixed — agents never change trading rules, they only guarantee
    that the existing rules run correctly;
  * deterministic python first, local LLM only as a fallback for text tasks;
  * every agent writes a JSON report and never blocks the market path;
  * when something cannot be fixed automatically it goes to state/escalation/,
    ready to be handed to the architect in ONE summarized batch.
"""
