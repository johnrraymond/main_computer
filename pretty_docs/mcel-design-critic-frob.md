# MCEL Design Critic Frob — dual AI/human visual review

This Frob lets **ChatGPT and a local Ollama vision model** answer the **same five design questions** against the same semantically preserved MCEL application. It is a design *experiment*, not automatic aesthetic certification or a policy mutator.

## Generate the packet

From the Main Computer repository root:

```text
python tools/mcel_layout_frob.py --mode design-review
```

This reuses the existing YAML → presentation planner → registered component tree → canonical MCEL layout engine. It produces three **different canonical compositions**, A/B/C, each captured in Chromium at 390px and 1440px in normal and synthetic verified-failure states. There are 12 full-page PNGs. It verifies matching component-tree fingerprints, component counts, safe operation state, working search/inspection, and lack of horizontal overflow. All images are hashed.

Output directory: `runtime/reports/mcel-layout-frob/design-review/`

- `gallery.html` — readable evidence gallery.
- `mcel-design-review.zip` — **portable packet for either reviewer** (screenshots, `packet.json`, `review-prompt.md`, `review-schema.json`, `review-template.json`, browser report).
- `browser-report.json` — bounded mechanical proof; *not* a visual-quality rating.
- `generated-A`, `generated-B`, `generated-C` — compiled disposable fixture-only MCEL applications (not shipped in packet).

You can generate directly as well:

```text
python tools/mcel_design_critic_frob.py --mode build
```

## Browser discovery on Windows

The Design Critic now uses the same portable Playwright launcher as semantic
verification. A browser executable **does not have to be on PATH**. Discovery
tries Playwright-managed Chromium, installed Microsoft Edge, installed Google
Chrome, and known executables. The selected browser version and launch method
are recorded in the packet for reproducibility. No browser is downloaded or
installed automatically.

Run the usual command:

```text
python tools/mcel_layout_frob.py --mode design-review
```

If there is no usable local Chromium-family browser, either install the
Playwright-managed one or explicitly point at Edge/Chrome:

```text
python -m playwright install chromium
python tools/mcel_layout_frob.py --mode design-review --chromium-executable "C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
```

Alternatively set `MCEL_CHROMIUM_EXECUTABLE` to an existing full executable
path. Explicit paths fail closed if missing or unlaunchable; the runner will not
silently switch browsers. The three `MCEL_SEMANTIC_GENERATION_UNVERIFIED`
messages at the start are expected: A/B/C are **compiler-only preparatory
builds**; the following Design Critic browser run is their acceptance test.
The final result must say `MCEL_DESIGN_CRITIC_PASS`, produce twelve hashed PNGs,
and report a browser version before it is a usable review packet.

## ChatGPT review

Upload the generated `mcel-design-review.zip` to ChatGPT and ask it to **read `review-prompt.md`, inspect the PNGs, compare candidates A/B/C, answer Q1–Q5, and return a JSON review using `review-template.json`**. It should cite image IDs for every concrete finding. `packet_sha256` is supplied in the prompt/template; neither reviewer should have to guess the fingerprint. Save the response as `chatgpt-review.json` in the repo or downloads folder.

Import the JSON (read-only validation, not a policy change):

```text
python tools/mcel_design_critic_frob.py --mode import --packet runtime/reports/mcel-layout-frob/design-review/mcel-design-review.zip --review chatgpt-review.json
```

## Local Gemma 26B vision review (Ollama)

Use the **same ZIP packet** and questions. Run the no-network preflight first:

```text
python tools/mcel_design_critic_frob.py --mode local --packet runtime/reports/mcel-layout-frob/design-review/mcel-design-review.zip --dry-run
```

Then, on the Windows machine with Ollama running and the reduced Gemma model installed:

```text
python tools/mcel_design_critic_frob.py --mode local --packet runtime/reports/mcel-layout-frob/design-review/mcel-design-review.zip --model gemma4:26b-a4b-it-q4_K_M
```

The local runner checks `/api/show` and **refuses** a model that does not advertise `vision`. It sends 3 matched images per call to Ollama `/api/chat` using `messages[].images`, `stream=false`, `format=json`, `think=false`, and `temperature=0.1`. Four calls cover mobile/desktop × normal/verified failure. It validates every referenced screenshot ID and returns `local/local-review.json`, subgroup reviews, and counted group preferences. The entire review can be inspected without uploading local images to any external service.

Image conversion uses Pillow if available, but falls back to original PNG bytes if Pillow is absent. Keep the Ollama endpoint local (`http://127.0.0.1:11434` by default). There is no remote inference fallback. A failure to parse the local model's JSON or a non-vision capability yields a **REFUSED** result, not a fabricated review or text-only visual rating.

## Compare critics

When `chatgpt-review.json` and `local/local-review.json` both exist:

```text
python tools/mcel_design_critic_frob.py --mode compare --packet runtime/reports/mcel-layout-frob/design-review/mcel-design-review.zip --review chatgpt-review.json --local-review runtime/reports/mcel-design-critic/local/local-review.json
```

Both outputs are linked to the **exact same packet SHA-256**. `critic-comparison.json` reports agreement/disagreement and still requires human review. The runner does **not** insert suggested policies into MCEL or edit generated applications.

## Review rubric and governance

Q1: Which candidate best serves the active task at desktop/mobile?
Q2: What evidence-backed hierarchy, density, readability, grouping, visual defects exist?
Q3: Are failures/unknowns honestly visible?
Q4: Which observations support *general* MCEL policy hypotheses (not Task Manager CSS)?
Q5: How could each hypothesis be independently falsified and where is evidence insufficient?

All three plans are structurally different on desktop. **A:** stacked workspace; **B:** attention rail and main workspace; **C:** status banner plus workspace/tools rail. On mobile C tests paired compact cards; B emphasizes compressed attention. All use the existing canonical MCEL resolver and the same semantic component tree. The original semantic authority, `require` hints, operation receipts and authorization remain unchanged.

Screenshots are from synthetic fixtures, not live data. A visually attractive layout may still be unusable for a real workflow; accessibility, real-world productivity and user preferences remain independent gates. Local and hosted models are untrusted critics; their output is recorded as hypotheses requiring explicit human judgment.

## Testing

```text
python -m unittest tests.test_mcel_design_critic_frob tests.test_mcel_task_composition tests.test_mcel_presentation_state tests.test_mcel_presentation_planner
```

Production Task Manager and Website Builder are not modified. `.md` documentation remains in `pretty_docs/`.
