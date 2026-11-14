# Balanced Hawks and Doves: numeric frontier, not strategy classification

**Current authoritative specification:** [QWEN_UNIFIED_T_HDB_COMPETENCY_FRONTIER.md](QWEN_UNIFIED_T_HDB_COMPETENCY_FRONTIER.md).

HDB means **Balanced Hawks and Doves**. The governing output is the **exact signed numerical balance around zero**, represented in integer half-point units, not a choice of Hawk/Dove/Tie and not separate X/Y payoff totals. HDB00–HDB06 are canonical signed-addition reductions that merge into existing T00/T01/T02/T03/T03/T05/T07 respectively. HDB07 supplies arithmetic with the encounter context; HDB08 requires reconstructing the balance from the narrative. The same signed balance is the mathematical invariant throughout.

This replaces the earlier `hdb-frontier` **strategy-classification pilot**. That earlier design used `ARGMAX` and labels `HAWK`, `DOVE`, `TIE`; it is superseded and must not be used to characterize numeric zero-line competence. Historical output and checkpoints are preserved as evidence but are not resumed into the corrected `v2` joint frontier.

Start or resume with `--stage run --frontier --frontier-hdb` and the complete explicit arguments in the linked specification. Read diagnostics with `--stage hdb-difficulty --frontier --frontier-hdb`. The arithmetic stress checkpoint remains the parent; both T and HDB continue in one shared training loop and LoRA optimizer state.
