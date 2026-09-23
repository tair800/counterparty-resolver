"""Acquisition and construction of the labelled pair corpus, kept apart on purpose.

`acquire` fetches and records; `build` constructs pairs. Splitting them is what makes it
structurally true that a label was never produced by the same code that produced the record it
labels.
"""
