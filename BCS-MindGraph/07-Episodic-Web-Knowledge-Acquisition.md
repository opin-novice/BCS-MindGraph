# 07. Episodic Web Knowledge Acquisition and Provenance

## Goal
Acquire external evidence in a way that preserves where it came from and when it was observed.

## Approach
Use episodic acquisition: collect knowledge as time-stamped episodes anchored to web sources.

## Provenance Requirements
Each fact should trace back to:
- source URL or document
- retrieval timestamp
- extraction time
- evidence fragment or sentence
- confidence indicator

## System Behavior
When new facts conflict with existing ones, the system must be designed to maintain the conflict rather than silently overwrite without provenance.

## Importance
This component supports both factual reliability and transparent reasoning under temporal change.
