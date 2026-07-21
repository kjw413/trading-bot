"""Value-investing valuation and decision core.

Pure, deterministic computation only — no network, no file access. Implements
the framework in docs/valuation-framework.md: valuations return a
(conservative, base, optimistic) tuple, signals come from IRR vs required
return, and the purchase cost basis never enters a decision function.
"""
