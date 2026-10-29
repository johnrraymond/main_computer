# MCEL Lifecycle L18 — Interactive Operator Guide

## Purpose

This lab is an operator-led experiment. The L18 Taguchi array chooses 18 balanced configurations; it does **not** decide whether the interface behaves correctly for a human. The primary experimental observation is the operator's direct interaction with each live MCEL collection specimen.

## Workflow

1. In MCEL Lab, select **Lifecycle Acid Test · L18**. L18-01 loads automatically. The specimen is a real read-only MCEL universal collection using offline Task Manager semantic fixtures.
2. Inspect the displayed configuration. Hover, click, select/copy text, expand and scroll directly inside the specimen. Use **Apply next case condition** to inject each case's prescribed interaction or source event one at a time, with time to inspect between events. The independent source-update, removal, stale-observation and transaction-failure controls remain available.
3. Mark **Works correctly**, **Something is wrong**, or **Needs investigation**. Record what you expected and what happened.
4. Use **Next**, **Previous**, the case picker, or **Try live** in the L18 matrix to move between cases. Every case opens a fresh specimen; judgments and notes remain associated with their L18 ID.
5. Use **Restart this experiment** to reset the specimen without changing its settings. Use **Advanced drill-in** to edit one or two factor levels and remount an *edited variant*. The baseline L18 matrix itself remains unchanged.
6. Open **Optional automated screening** to run a separate scripted invariant check, and **Export JSON evidence** to download both automated results and your 18 per-case judgments with interaction histories. Export before closing or reloading the page; manual notes are held in page memory only.

## What is and is not tested

A PASS in the automated matrix means scripted invariants passed. It is not a substitute for human inspection. The specimen runs real MCEL layout, presentation negotiation, selection, retirement, and source-observation code, but operates on offline, read-only fixtures. The lab does not exercise the production Task Manager route, concurrent worker scheduling, virtualized lists, or accessibility compliance across all browsers.

## Reproducibility

Each human review includes the L18 case ID, untouched Taguchi factor assignment, verdict, notes, and recorded interaction events. Edits to a case's factor controls are recorded with events but do not overwrite the original L18 assignment.
