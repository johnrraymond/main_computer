# Hawk–Dove million-count T08 → T00: one audited mutation flow

Fixed teacher inputs `a=200000`, `b=800000`; oracle regression targets `X=2200000`, `Y=1200000`. The payoff numbers are teacher-only; the four payoff symbols stay hidden in model-facing prompts.

Within each tier the sequence is full raw+LogBar → one LogBar stripped per step → fully raw. T01 and T02 are alternative partial-expansion branches.

## T08

**Start: all full**

```text
You realize you are playing Hawk–Dove.

Your opponent plays Hawk 200000, ||||||||||||.|| out of a million times.
Your opponent plays Dove 800000, |||||||||||||.|||||| out of the same million times.

Which pure strategy gives you the greater total payoff across those encounters?
```

**Mutation 1: strip a0**

```text
You realize you are playing Hawk–Dove.

Your opponent plays Hawk 200000 out of a million times.
Your opponent plays Dove 800000, |||||||||||||.|||||| out of the same million times.

Which pure strategy gives you the greater total payoff across those encounters?
```

**End: all raw**

```text
You realize you are playing Hawk–Dove.

Your opponent plays Hawk 200000 out of a million times.
Your opponent plays Dove 800000 out of the same million times.

Which pure strategy gives you the greater total payoff across those encounters?
```

## T07

**Start: all full**

```text
You realize you are playing Hawk–Dove.

Your opponent plays Hawk 200000, ||||||||||||.|| out of a million times.
Your opponent plays Dove 800000, |||||||||||||.|||||| out of the same million times.

Which pure strategy gives you the greater total payoff across those encounters?

(ARGMAX
  (ADD
    (MUL 200000, ||||||||||||.|| HAWK_HAWK_PAYOFF)
    (MUL 800000, |||||||||||||.|||||| HAWK_DOVE_PAYOFF))
  (ADD
    (MUL 200000, ||||||||||||.|| DOVE_HAWK_PAYOFF)
    (MUL 800000, |||||||||||||.|||||| DOVE_DOVE_PAYOFF)))
```

**Mutation 1: strip a0**

```text
You realize you are playing Hawk–Dove.

Your opponent plays Hawk 200000 out of a million times.
Your opponent plays Dove 800000, |||||||||||||.|||||| out of the same million times.

Which pure strategy gives you the greater total payoff across those encounters?

(ARGMAX
  (ADD
    (MUL 200000, ||||||||||||.|| HAWK_HAWK_PAYOFF)
    (MUL 800000, |||||||||||||.|||||| HAWK_DOVE_PAYOFF))
  (ADD
    (MUL 200000, ||||||||||||.|| DOVE_HAWK_PAYOFF)
    (MUL 800000, |||||||||||||.|||||| DOVE_DOVE_PAYOFF)))
```

**Mutation 2: strip b0**

```text
You realize you are playing Hawk–Dove.

Your opponent plays Hawk 200000 out of a million times.
Your opponent plays Dove 800000 out of the same million times.

Which pure strategy gives you the greater total payoff across those encounters?

(ARGMAX
  (ADD
    (MUL 200000, ||||||||||||.|| HAWK_HAWK_PAYOFF)
    (MUL 800000, |||||||||||||.|||||| HAWK_DOVE_PAYOFF))
  (ADD
    (MUL 200000, ||||||||||||.|| DOVE_HAWK_PAYOFF)
    (MUL 800000, |||||||||||||.|||||| DOVE_DOVE_PAYOFF)))
```

**Mutation 3: strip a1**

```text
You realize you are playing Hawk–Dove.

Your opponent plays Hawk 200000 out of a million times.
Your opponent plays Dove 800000 out of the same million times.

Which pure strategy gives you the greater total payoff across those encounters?

(ARGMAX
  (ADD
    (MUL 200000 HAWK_HAWK_PAYOFF)
    (MUL 800000, |||||||||||||.|||||| HAWK_DOVE_PAYOFF))
  (ADD
    (MUL 200000, ||||||||||||.|| DOVE_HAWK_PAYOFF)
    (MUL 800000, |||||||||||||.|||||| DOVE_DOVE_PAYOFF)))
```

**Mutation 4: strip b1**

```text
You realize you are playing Hawk–Dove.

Your opponent plays Hawk 200000 out of a million times.
Your opponent plays Dove 800000 out of the same million times.

Which pure strategy gives you the greater total payoff across those encounters?

(ARGMAX
  (ADD
    (MUL 200000 HAWK_HAWK_PAYOFF)
    (MUL 800000 HAWK_DOVE_PAYOFF))
  (ADD
    (MUL 200000, ||||||||||||.|| DOVE_HAWK_PAYOFF)
    (MUL 800000, |||||||||||||.|||||| DOVE_DOVE_PAYOFF)))
```

**Mutation 5: strip a2**

```text
You realize you are playing Hawk–Dove.

Your opponent plays Hawk 200000 out of a million times.
Your opponent plays Dove 800000 out of the same million times.

Which pure strategy gives you the greater total payoff across those encounters?

(ARGMAX
  (ADD
    (MUL 200000 HAWK_HAWK_PAYOFF)
    (MUL 800000 HAWK_DOVE_PAYOFF))
  (ADD
    (MUL 200000 DOVE_HAWK_PAYOFF)
    (MUL 800000, |||||||||||||.|||||| DOVE_DOVE_PAYOFF)))
```

**End: all raw**

```text
You realize you are playing Hawk–Dove.

Your opponent plays Hawk 200000 out of a million times.
Your opponent plays Dove 800000 out of the same million times.

Which pure strategy gives you the greater total payoff across those encounters?

(ARGMAX
  (ADD
    (MUL 200000 HAWK_HAWK_PAYOFF)
    (MUL 800000 HAWK_DOVE_PAYOFF))
  (ADD
    (MUL 200000 DOVE_HAWK_PAYOFF)
    (MUL 800000 DOVE_DOVE_PAYOFF)))
```

## T06

**Start: all full**

```lisp
(ARGMAX
  (ADD
    (MUL HAWK_HAWK_PAYOFF 200000, ||||||||||||.||)
    (MUL HAWK_DOVE_PAYOFF 800000, |||||||||||||.||||||))
  (ADD
    (MUL DOVE_HAWK_PAYOFF 200000, ||||||||||||.||)
    (MUL DOVE_DOVE_PAYOFF 800000, |||||||||||||.||||||)))
```

**Mutation 1: strip a0**

```lisp
(ARGMAX
  (ADD
    (MUL HAWK_HAWK_PAYOFF 200000)
    (MUL HAWK_DOVE_PAYOFF 800000, |||||||||||||.||||||))
  (ADD
    (MUL DOVE_HAWK_PAYOFF 200000, ||||||||||||.||)
    (MUL DOVE_DOVE_PAYOFF 800000, |||||||||||||.||||||)))
```

**Mutation 2: strip b0**

```lisp
(ARGMAX
  (ADD
    (MUL HAWK_HAWK_PAYOFF 200000)
    (MUL HAWK_DOVE_PAYOFF 800000))
  (ADD
    (MUL DOVE_HAWK_PAYOFF 200000, ||||||||||||.||)
    (MUL DOVE_DOVE_PAYOFF 800000, |||||||||||||.||||||)))
```

**Mutation 3: strip a1**

```lisp
(ARGMAX
  (ADD
    (MUL HAWK_HAWK_PAYOFF 200000)
    (MUL HAWK_DOVE_PAYOFF 800000))
  (ADD
    (MUL DOVE_HAWK_PAYOFF 200000)
    (MUL DOVE_DOVE_PAYOFF 800000, |||||||||||||.||||||)))
```

**End: all raw**

```lisp
(ARGMAX
  (ADD
    (MUL HAWK_HAWK_PAYOFF 200000)
    (MUL HAWK_DOVE_PAYOFF 800000))
  (ADD
    (MUL DOVE_HAWK_PAYOFF 200000)
    (MUL DOVE_DOVE_PAYOFF 800000)))
```

## T05

**Start: all full**

```lisp
(ARGMAX
  (ADD
    (MUL 200000, ||||||||||||.|| HAWK_HAWK_PAYOFF)
    (MUL 800000, |||||||||||||.|||||| HAWK_DOVE_PAYOFF))
  (ADD
    (MUL 800000, |||||||||||||.|||||| DOVE_DOVE_PAYOFF)
    (MUL 200000, ||||||||||||.|| DOVE_HAWK_PAYOFF)))
```

**Mutation 1: strip a0**

```lisp
(ARGMAX
  (ADD
    (MUL 200000 HAWK_HAWK_PAYOFF)
    (MUL 800000, |||||||||||||.|||||| HAWK_DOVE_PAYOFF))
  (ADD
    (MUL 800000, |||||||||||||.|||||| DOVE_DOVE_PAYOFF)
    (MUL 200000, ||||||||||||.|| DOVE_HAWK_PAYOFF)))
```

**Mutation 2: strip b0**

```lisp
(ARGMAX
  (ADD
    (MUL 200000 HAWK_HAWK_PAYOFF)
    (MUL 800000 HAWK_DOVE_PAYOFF))
  (ADD
    (MUL 800000, |||||||||||||.|||||| DOVE_DOVE_PAYOFF)
    (MUL 200000, ||||||||||||.|| DOVE_HAWK_PAYOFF)))
```

**Mutation 3: strip b1**

```lisp
(ARGMAX
  (ADD
    (MUL 200000 HAWK_HAWK_PAYOFF)
    (MUL 800000 HAWK_DOVE_PAYOFF))
  (ADD
    (MUL 800000 DOVE_DOVE_PAYOFF)
    (MUL 200000, ||||||||||||.|| DOVE_HAWK_PAYOFF)))
```

**End: all raw**

```lisp
(ARGMAX
  (ADD
    (MUL 200000 HAWK_HAWK_PAYOFF)
    (MUL 800000 HAWK_DOVE_PAYOFF))
  (ADD
    (MUL 800000 DOVE_DOVE_PAYOFF)
    (MUL 200000 DOVE_HAWK_PAYOFF)))
```

## T04

**Start: all full**

```lisp
(ARGMAX
  (ADD
    (MUL 800000, |||||||||||||.|||||| HAWK_DOVE_PAYOFF)
    (MUL 200000, ||||||||||||.|| HAWK_HAWK_PAYOFF))
  (ADD
    (MUL 200000, ||||||||||||.|| DOVE_HAWK_PAYOFF)
    (MUL 800000, |||||||||||||.|||||| DOVE_DOVE_PAYOFF)))
```

**Mutation 1: strip b0**

```lisp
(ARGMAX
  (ADD
    (MUL 800000 HAWK_DOVE_PAYOFF)
    (MUL 200000, ||||||||||||.|| HAWK_HAWK_PAYOFF))
  (ADD
    (MUL 200000, ||||||||||||.|| DOVE_HAWK_PAYOFF)
    (MUL 800000, |||||||||||||.|||||| DOVE_DOVE_PAYOFF)))
```

**Mutation 2: strip a0**

```lisp
(ARGMAX
  (ADD
    (MUL 800000 HAWK_DOVE_PAYOFF)
    (MUL 200000 HAWK_HAWK_PAYOFF))
  (ADD
    (MUL 200000, ||||||||||||.|| DOVE_HAWK_PAYOFF)
    (MUL 800000, |||||||||||||.|||||| DOVE_DOVE_PAYOFF)))
```

**Mutation 3: strip a1**

```lisp
(ARGMAX
  (ADD
    (MUL 800000 HAWK_DOVE_PAYOFF)
    (MUL 200000 HAWK_HAWK_PAYOFF))
  (ADD
    (MUL 200000 DOVE_HAWK_PAYOFF)
    (MUL 800000, |||||||||||||.|||||| DOVE_DOVE_PAYOFF)))
```

**End: all raw**

```lisp
(ARGMAX
  (ADD
    (MUL 800000 HAWK_DOVE_PAYOFF)
    (MUL 200000 HAWK_HAWK_PAYOFF))
  (ADD
    (MUL 200000 DOVE_HAWK_PAYOFF)
    (MUL 800000 DOVE_DOVE_PAYOFF)))
```

## T03

**Start: all full**

```lisp
(ARGMAX
  (ADD
    (MUL 200000, ||||||||||||.|| HAWK_HAWK_PAYOFF)
    (MUL 800000, |||||||||||||.|||||| HAWK_DOVE_PAYOFF))
  (ADD
    (MUL 200000, ||||||||||||.|| DOVE_HAWK_PAYOFF)
    (MUL 800000, |||||||||||||.|||||| DOVE_DOVE_PAYOFF)))
```

**Mutation 1: strip a0**

```lisp
(ARGMAX
  (ADD
    (MUL 200000 HAWK_HAWK_PAYOFF)
    (MUL 800000, |||||||||||||.|||||| HAWK_DOVE_PAYOFF))
  (ADD
    (MUL 200000, ||||||||||||.|| DOVE_HAWK_PAYOFF)
    (MUL 800000, |||||||||||||.|||||| DOVE_DOVE_PAYOFF)))
```

**Mutation 2: strip b0**

```lisp
(ARGMAX
  (ADD
    (MUL 200000 HAWK_HAWK_PAYOFF)
    (MUL 800000 HAWK_DOVE_PAYOFF))
  (ADD
    (MUL 200000, ||||||||||||.|| DOVE_HAWK_PAYOFF)
    (MUL 800000, |||||||||||||.|||||| DOVE_DOVE_PAYOFF)))
```

**Mutation 3: strip a1**

```lisp
(ARGMAX
  (ADD
    (MUL 200000 HAWK_HAWK_PAYOFF)
    (MUL 800000 HAWK_DOVE_PAYOFF))
  (ADD
    (MUL 200000 DOVE_HAWK_PAYOFF)
    (MUL 800000, |||||||||||||.|||||| DOVE_DOVE_PAYOFF)))
```

**End: all raw**

```lisp
(ARGMAX
  (ADD
    (MUL 200000 HAWK_HAWK_PAYOFF)
    (MUL 800000 HAWK_DOVE_PAYOFF))
  (ADD
    (MUL 200000 DOVE_HAWK_PAYOFF)
    (MUL 800000 DOVE_DOVE_PAYOFF)))
```

## T02

**Start: all full**

```lisp
(ARGMAX
  (ADD
    (MUL 200000, ||||||||||||.|| HAWK_HAWK_PAYOFF)
    (MUL 800000, |||||||||||||.|||||| HAWK_DOVE_PAYOFF))
  1200000, ||||||||||||||.)
```

**Mutation 1: strip a0**

```lisp
(ARGMAX
  (ADD
    (MUL 200000 HAWK_HAWK_PAYOFF)
    (MUL 800000, |||||||||||||.|||||| HAWK_DOVE_PAYOFF))
  1200000, ||||||||||||||.)
```

**Mutation 2: strip b0**

```lisp
(ARGMAX
  (ADD
    (MUL 200000 HAWK_HAWK_PAYOFF)
    (MUL 800000 HAWK_DOVE_PAYOFF))
  1200000, ||||||||||||||.)
```

**End: all raw**

```lisp
(ARGMAX
  (ADD
    (MUL 200000 HAWK_HAWK_PAYOFF)
    (MUL 800000 HAWK_DOVE_PAYOFF))
  1200000)
```

## T01

**Start: all full**

```lisp
(ARGMAX
  2200000, ||||||||||||||.||||||
  (ADD
    (MUL 200000, ||||||||||||.|| DOVE_HAWK_PAYOFF)
    (MUL 800000, |||||||||||||.|||||| DOVE_DOVE_PAYOFF)))
```

**Mutation 1: strip x0**

```lisp
(ARGMAX
  2200000
  (ADD
    (MUL 200000, ||||||||||||.|| DOVE_HAWK_PAYOFF)
    (MUL 800000, |||||||||||||.|||||| DOVE_DOVE_PAYOFF)))
```

**Mutation 2: strip a0**

```lisp
(ARGMAX
  2200000
  (ADD
    (MUL 200000 DOVE_HAWK_PAYOFF)
    (MUL 800000, |||||||||||||.|||||| DOVE_DOVE_PAYOFF)))
```

**End: all raw**

```lisp
(ARGMAX
  2200000
  (ADD
    (MUL 200000 DOVE_HAWK_PAYOFF)
    (MUL 800000 DOVE_DOVE_PAYOFF)))
```

## T00

**Start: all full**

```lisp
(ARGMAX
  2200000, ||||||||||||||.||||||
  1200000, ||||||||||||||.)
```

**Mutation 1: strip x0**

```lisp
(ARGMAX
  2200000
  1200000, ||||||||||||||.)
```

**End: all raw**

```lisp
(ARGMAX
  2200000
  1200000)
```

## Invariants

- Every prompt has the same teacher X and Y targets for this source instance.
- Two distinct numerical values except the three-value partial-expansion T01 and T02.
- Each mutation removes exactly one LogBar from a single numeric occurrence.
- Raw + LogBar codes are locally extended using `floor(10*log1p(abs(n)) + 0.5)`.
- T00 contains only raw numerical payoff targets; no encounter counts are rendered in T00.
- T08 contains only raw English and the two opponent-count numerical inputs.
