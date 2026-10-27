# HybridBeast R0a/R0b/R0c — genuine sampled TRAIN examples

Generated deterministically from the v3 corpus with seed 20261027.
All rows are actual TRAIN samples with full two-card invariance pairs.
The numbers shown as expected outputs are oracle targets, **not QW5 predictions**.
A numerical input pair is supplied to EMP; the question goes to Qwen.

| Rung | Eligible signed target range | TRAIN / DEV / TEST |
|---|---|---|
| R0a | ±4,096 | 512 / 128 / 128 |
| R0b | ±100,000 | 512 / 128 / 128 |
| R0c | ±1,000,000 | 512 / 128 / 128 |

## R0a — exact ceiling ±4,096

Both orientations shown; same target under operand commutativity.

**Target `+0`**

- EMP operands `(1114649, -1114649)`; Qwen prompt: "A location starts at 1114649 and changes by -1114649. Return the signed final position." → `+0`
- EMP operands `(-1114649, 1114649)`; Qwen prompt: "Compute the signed sum of -1114649 and 1114649. Return the exact result." → `+0`

**Target `+8`**

- EMP operands `(1233939, -1233931)`; Qwen prompt: "Compute the signed sum of 1233939 and -1233931. Return the exact result." → `+8`
- EMP operands `(-1233931, 1233939)`; Qwen prompt: "A location starts at -1233931 and changes by 1233939. Return the signed final position." → `+8`

**Target `-8`**

- EMP operands `(1208378, -1208386)`; Qwen prompt: "Compute the signed sum of 1208378 and -1208386. Return the exact result." → `-8`
- EMP operands `(-1208386, 1208378)`; Qwen prompt: "A location starts at -1208386 and changes by 1208378. Return the signed final position." → `-8`

**Target `+4096`**

- EMP operands `(1227371, -1223275)`; Qwen prompt: "Compute the signed sum of 1227371 and -1223275. Return the exact result." → `+4096`
- EMP operands `(-1223275, 1227371)`; Qwen prompt: "A location starts at -1223275 and changes by 1227371. Return the signed final position." → `+4096`

**Target `-4096`**

- EMP operands `(-895800, 891704)`; Qwen prompt: "Compute the signed sum of -895800 and 891704. Return the exact result." → `-4096`
- EMP operands `(891704, -895800)`; Qwen prompt: "A location starts at 891704 and changes by -895800. Return the signed final position." → `-4096`

## R0b — exact ceiling ±100,000

Both orientations shown; same target under operand commutativity.

**Target `+0`**

- EMP operands `(1233931, -1233931)`; Qwen prompt: "Compute the signed sum of 1233931 and -1233931. Return the exact result." → `+0`
- EMP operands `(-1233931, 1233931)`; Qwen prompt: "A location starts at -1233931 and changes by 1233931. Return the signed final position." → `+0`

**Target `+8`**

- EMP operands `(-1644152, 1644160)`; Qwen prompt: "A location starts at -1644152 and changes by 1644160. Return the signed final position." → `+8`
- EMP operands `(1644160, -1644152)`; Qwen prompt: "Compute the signed sum of 1644160 and -1644152. Return the exact result." → `+8`

**Target `-8`**

- EMP operands `(-1931418, 1931410)`; Qwen prompt: "Compute the signed sum of -1931418 and 1931410. Return the exact result." → `-8`
- EMP operands `(1931410, -1931418)`; Qwen prompt: "A location starts at 1931410 and changes by -1931418. Return the signed final position." → `-8`

**Target `+100000`**

- EMP operands `(580964, -480964)`; Qwen prompt: "Compute the signed sum of 580964 and -480964. Return the exact result." → `+100000`
- EMP operands `(-480964, 580964)`; Qwen prompt: "A location starts at -480964 and changes by 580964. Return the signed final position." → `+100000`

**Target `-100000`**

- EMP operands `(429853, -529853)`; Qwen prompt: "Compute the signed sum of 429853 and -529853. Return the exact result." → `-100000`
- EMP operands `(-529853, 429853)`; Qwen prompt: "A location starts at -529853 and changes by 429853. Return the signed final position." → `-100000`

## R0c — exact ceiling ±1,000,000

Both orientations shown; same target under operand commutativity.

**Target `+0`**

- EMP operands `(-883317, 883317)`; Qwen prompt: "A location starts at -883317 and changes by 883317. Return the signed final position." → `+0`
- EMP operands `(883317, -883317)`; Qwen prompt: "Compute the signed sum of 883317 and -883317. Return the exact result." → `+0`

**Target `+8`**

- EMP operands `(-733484, 733492)`; Qwen prompt: "Compute the signed sum of -733484 and 733492. Return the exact result." → `+8`
- EMP operands `(733492, -733484)`; Qwen prompt: "A location starts at 733492 and changes by -733484. Return the signed final position." → `+8`

**Target `-8`**

- EMP operands `(-883325, 883317)`; Qwen prompt: "Compute the signed sum of -883325 and 883317. Return the exact result." → `-8`
- EMP operands `(883317, -883325)`; Qwen prompt: "A location starts at 883317 and changes by -883325. Return the signed final position." → `-8`

**Target `+1000000`**

- EMP operands `(-375122, 1375122)`; Qwen prompt: "Compute the signed sum of -375122 and 1375122. Return the exact result." → `+1000000`
- EMP operands `(1375122, -375122)`; Qwen prompt: "A location starts at 1375122 and changes by -375122. Return the signed final position." → `+1000000`

**Target `-1000000`**

- EMP operands `(240451, -1240451)`; Qwen prompt: "Compute the signed sum of 240451 and -1240451. Return the exact result." → `-1000000`
- EMP operands `(-1240451, 240451)`; Qwen prompt: "A location starts at -1240451 and changes by 240451. Return the signed final position." → `-1000000`

## Number of distinct selected targets and anchor families

| Rung | Split | Distinct signed target values | Distinct anchor families |
|---|---|---:|---:|
| R0a | train | 54 | 100 |
| R0a | dev | 38 | 37 |
| R0a | test | 30 | 24 |
| R0b | train | 73 | 102 |
| R0b | dev | 40 | 32 |
| R0b | test | 39 | 28 |
| R0c | train | 98 | 97 |
| R0c | dev | 52 | 29 |
| R0c | test | 48 | 28 |

Far residual values are disjoint across TRAIN/DEV/TEST within each rung.
