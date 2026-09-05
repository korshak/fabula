"""Fabula — a tiny AI-first DSL for narrative agents that write books.

The pipeline: .fab source -> parser -> compiler (JSON graph + machine-readable
diagnostics) -> prompt builder -> writer agent (Claude API) -> scene variants
+ self-critique report.
"""

__version__ = "0.1.0"
