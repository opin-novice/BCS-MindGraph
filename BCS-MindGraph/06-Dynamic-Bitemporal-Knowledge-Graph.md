# 06. Dynamic Bitemporal Knowledge Graph

## Goal
Maintain a knowledge graph that preserves both event time and assertion time.

## Core Concept
The graph must be bitemporal: it must store both
- when a fact is true in the world
- when the system learns or asserts that fact

## Why This Is Critical
This allows the system to distinguish:
- historical truth
- updated understanding
- provenance and trust over time

## Graph Elements
- entities
- relations
- facts
- temporal validity windows
- provenance links
- source metadata
- confidence or evidence strength

## Maintenance Requirements
The graph must support dynamic updates as new web evidence arrives and older evidence is revised or superseded.

## Research Contribution
This is the core technical novelty: temporal memory with provenance and update semantics rather than flat retrieval over static documents.
