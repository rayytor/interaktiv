"""
Training support for the hotspot detector: corpus, cache and splits.

Nothing in here is imported by the shipped detector or by the reader. It exists
so that the detector's constants can be fitted on this machine, against ground
truth derived from the corpus and the publisher's own metadata rather than from
hand-drawn boxes.
"""
