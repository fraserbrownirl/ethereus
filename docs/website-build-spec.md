# Ethereus Website — Build Spec for Agents

Oct 31, 2026 · @Fraser Brown

## Purpose and audience

The site exists to make one claim land in under ten seconds: **an EVM agent whose guardrails and arithmetic live in its weights, where a prompt can't reach them.** Everything else on the site supports that claim with mechanism, evidence and a roadmap.

Three audiences, in priority order:

1. **Agent-wallet and DeFi builders** deciding what to put their agents on. They need to understand the policy gate and the exactness guarantee, and to see that it runs today on Qwen3.5-4B.
2. **Hackathon participants and judges.** They need the how-it-works page, the repo link, and a reason the project is not a toy.
3. **AI researchers and the compiled-weights community** (the Qwenputer and nesformer audience). They need accurate mechanism language, numbers with sources, and credit to the original work.

What the site is not: a token page, a waitlist funnel, or a general "AI × crypto" pitch. No token, no fundraising copy, no roadmap dates beyond what the build spec commits to.

Tone: plain, specific, numeric. Every claim either carries a number with a source or is phrased as what the build is designed to do. The companion document, *Ethereus — Build Spec for Agents*, is the source of truth for every technical statement; where this spec and that one disagree, the build spec wins.

## Site map

Five routes. The home page carries the argument; the others carry the detail a visitor clicks into. No blog, no docs portal, no dashboard in v1.

| Route | Job | Sections, in order |
| --- | --- | --- |
| `/` | Land the claim, prove it in three blocks, send to repo | Hero · The three things (guardrail, exactness, knowledge) · How it works (short, with diagram) · Evidence strip (numbers) · Roadmap teaser · Credits + repo CTA |
| `/how-it-works` | Explain the mechanism to a builder in five minutes | The CPU in the weights · DeltaNet state as RAM · The tool bridge and position ownership · Why it's exact · What it costs (speed, limits) · Diagram (full size) |
| `/roadmap` | Show the path from Qwen3.5-4B to a frontier backbone, and why | Timeline diagram · One paragraph per step · What never changes (firmware, tools, policy) |
| `/build` | Get a builder to the code | Repo link · What runs today · Phase status (reads from a JSON the build agent updates) · How to run the demo · How to contribute |
| `/credits` | Give the original work its due | Qwenputer and its author · nesformer · ethskills · Qwen3.5 · Kimi papers · licenses |

Global elements: a top nav with the five routes and a GitHub link; a footer with license, credits link and a one-line disclaimer (see Claims policy). No cookie banner is needed if no analytics are loaded; if analytics are added, use a cookie-free tool.

Each page has one primary CTA and it is always "View the code" (repo link). Secondary CTAs go to `/how-it-works` or `/roadmap`. There is no email capture in v1.

## Home page copy

Use this copy as written. Headings are final; body text may be tightened but not expanded. Anything in \[brackets\] is filled from the build's measured numbers before launch.

**Hero**

Headline: *Ethereum in the weights.*

Subhead: *An EVM agent whose spending rules, hashes and arithmetic run inside the model's own layers. No tool server to trust, no prompt that can switch them off.*

Buttons: **View the code** (primary) · **How it works** (secondary)

Under the buttons, one line: *Built on Qwenputer: a RISC-V CPU compiled into Qwen3.5-4B. Nobody trained these layers; a compiler computed every weight.*

**The three things** (three columns or stacked cards; each has a title, one paragraph, one proof line)

1. **A guardrail a prompt can't reach.** Every agent wallet today enforces its spending policy outside the model: a prompt, a middleware, a server. All of them can be talked around. Ethereus checks the transaction on positions in the token stream that the language model's layers physically cannot write. "The monitor is off" is just text; the circuit doesn't read it. The policy is public, fixed in the weights, and every approval is a replayable trace. Proof line: *\[50\] injection prompts, \[0\] forged approvals.*
2. **Exact, and provable.** Agents hallucinate selectors, decimals and wei math. A Python tool fixes that, but then the agent trusts whatever the server sends back. Here keccak, ABI encoding and uint256 arithmetic run inside the forward pass and the trace is the proof. The original Qwenputer replayed \~230,000 circuit positions through a reference machine with zero mismatches. Proof line: *\[2,000\] random cases, \[0\] wrong answers, replay clean.*
3. **Knowledge that costs no context.** Models get Ethereum wrong: gas prices, EIP status, L2 costs. The ethskills knowledge base fixes that at \~50k tokens of context per conversation. Ethereus holds it in the model's own recurrent state: zero context cost, and a version a prompt injection can't swap. Proof line: *\[60\]-question Ethereum benchmark: base \[x\]%, Ethereus \[y\]%, at zero added context.*

**How it works** (short version; the full page is `/how-it-works`)

Qwen3.5 mixes attention layers with Gated DeltaNet layers, and each DeltaNet head keeps a 128 × 128 state matrix it updates every token. With a one-hot key and the gate held open, an update overwrites exactly one row. That's a RAM write. A one-hot query reads it back. That's a RAM read. Qwenputer builds a 32-bit RISC-V CPU out of eleven such layers, with its registers, memory and program counter living in that state. Ethereus writes firmware for that CPU: keccak, ABI, uint256, a small EVM, a signing-policy check. When the model calls a tool, the circuit runs the firmware and writes the answer itself, token by token. \[Diagram here: see How it works section.\]

**Evidence strip** (one row of four numbers, each with a caption and a source link)

- **0** mismatches in \[230k\] replayed circuit positions — Qwenputer
- **\[3,700\]** instructions/s on one L4 — the compiled CPU's speed
- **\[0\]** forged approvals in \[50\] injection attempts — Ethereus policy gate
- **\~\[6\]e-4** KL vs base Qwen on plain text — the model's language behaviour is unchanged

**Roadmap teaser**

*Runs today on Qwen3.5-4B. The firmware never changes; the backbone does. Kimi K3 uses the same gated delta mechanism, which is the path to a frontier-class model with the same guarantees.* → `/roadmap`

**Credits block (home)**

*Ethereus stands on Qwenputer by Divyansh Agrawal, nesformer by Drew Breunig, and ethskills by Austin Griffith. Code MIT, weights Apache-2.0.* → `/credits`

## How it works page

Five sections, each a heading, two to four short paragraphs, and one figure where marked. Write for a smart builder who has never opened a transformer. No equations; name the mechanism, state the number, link the source.

**1. A CPU made of transformer layers.** Qwenputer puts eleven extra layers in front of Qwen3.5-4B's own thirty-two. None of their weights are learned; a compiler computes each one from a circuit description. The MLPs act as exact lookup tables, the attention heads as wiring. The circuit is about one million nonzero weights. It passes the official riscv-tests suites and runs gcc-compiled C.

**2. The model's memory as RAM.** Qwen3.5's Gated DeltaNet layers keep a running state that the delta rule can overwrite one row at a time, exactly, so each row works as a memory cell. With the gate held open and a one-hot key, one update replaces exactly one row; a one-hot query returns it. The CPU keeps its code, data, registers and program counter there: 256 KB of code ROM, 128 KB of read-only data, 32 KB of RAM, all in state the model already has. \[Figure A\]

**3. The tool bridge and who owns which positions.** Qwen writes a `<tool_call>` block exactly as it would for any tool. Nothing outside the model runs it. The compiled layers read the call text, run the firmware, and write the `<tool_response>` themselves, one token at a time. During those positions the circuit owns the output and Qwen's own layers are frozen; everywhere else, Qwen predicts. That ownership is recorded per token, and it is what makes the policy gate unforgeable: an approval is only valid on a circuit-owned position. \[Figure B\]

**4. Why the answers are exact.** The circuit's arithmetic is exact in bf16 by construction, and every circuit token is checked in lockstep against a reference machine. Qwenputer replayed about 230,000 output positions with zero mismatches, and the model's plain-text behaviour stays within noise of base Qwen (KL 6e-4 against a 5e-4 floor). Ethereus inherits all of that; it adds firmware, not weights.

**5. What it costs.** About 1.3 trace tokens per instruction and \~3,700 instructions per second on one L4. A keccak takes seconds, an ERC-20 call tens of seconds, a Merkle proof minutes. A tool call can carry about 80 tokens of input; anything larger (bytecode, proofs, skills) lives in the in-weight filesystem. These are the limits of a 4B model with a compiled 32-bit CPU, not of the idea. \[Link to the limits table in the build spec.\]

**Figure A, "One row is one byte"** (SVG, light and dark variants): a 128 × 128 grid labelled *DeltaNet head state*; one row highlighted; an arrow in from a box *one-hot key* labelled *write (β = 1, α = 1)*; an arrow out to a box *one-hot query* labelled *read*. Beneath, three small grids labelled *registers*, *RAM*, *ROM* with a caption: *the CPU's whole memory is spread across a few dozen heads*. Redraw from the Qwenputer README's `gdn-ram.png`; do not copy the image.

**Figure B, "Who writes each token"** (SVG): a horizontal token stream. Left segment coloured *Qwen*: `…encode a transfer of 1.5 USDC to 0xAB…` then `<tool_call> eth encode …`. Middle segment coloured *circuit*: a burst of small boxes labelled *trace tokens* then `<tool_response> 0xa9059cbb…`. Right segment coloured *Qwen* again: `The calldata is 0xa9059cbb…`. Above the circuit segment a bracket: *circuit-owned positions: Qwen's layers frozen here*. Below: *the policy gate's APPROVED line can only appear in this segment*. This figure is reused at half size on the home page.

All numbers on this page come from the Qwenputer README and `experimental/riscv/docs/rv32.md`; link both at the bottom of the page.

## Roadmap page copy

The page makes one argument: *the firmware is finished once; the backbone is swappable.* Open with that sentence, then the timeline figure, then one block per step. No dates. Each step has a status chip read from `status.json` (`done`, `in progress`, `planned`).

**Figure C, "Same firmware, bigger brain"** (SVG timeline, four nodes left to right, a constant bar underneath): nodes *Qwen3.5-4B* → *Qwen3.5 (larger)* → *Kimi Linear 48B-A3B* → *Kimi K3 (2.8T, 104B active)*. Under all four, one unbroken bar labelled *eth · files · sign · verify firmware — unchanged*. Each node has a one-line label beneath: *runs today* / *same compiler, wider merge* / *KDA port, MoE placement* / *sidecar executor, no re-merge*. Status chip on each node.

**Step 1 — Qwen3.5-4B (today).** The published Qwenputer checkpoint. Ethereus's firmware runs on it unchanged. A 4B model is a modest reasoner, but every guarantee on this site is already true here: unforgeable policy, exact arithmetic, zero-context knowledge.

**Step 2 — a larger Qwen3.5.** Same compiler, same merge script; only the width changes. This is a better reasoner on the same firmware with no new research, and it proves the compiled layers survive a bigger backbone.

**Step 3 — Kimi Linear 48B-A3B.** Moonshot's open model built on Kimi Delta Attention: a gated delta rule with a per-channel forget gate, a superset of what the RAM trick needs. New work is in the compiler (KDA gate shapes), in placing compiled lookup tables inside a mixture-of-experts block, and in re-proving bit-exactness on Kimi's kernels. This is where the risk lives, and it fits on one or two 80 GB GPUs.

**Step 4 — Kimi K3.** 2.8 trillion parameters, 104 billion active, three KDA layers for every attention layer, open weights. Because the executor already runs the circuit outside the backbone's own modules and injects the result, the circuit can run as a sidecar on one GPU beside a multi-node K3; no 2.8T re-save. A frontier-class reasoner that still cannot forge an approval or get a keccak wrong.

**What never changes** (closing block): the firmware, the four tools, the tests, the policy format, the attestation. A reader should leave knowing that choosing Ethereus today does not lock them to a 4B model.

Source links at the bottom: Kimi K3 technical report (arXiv 2607.24653), Kimi Linear paper (arXiv 2510.26692), Qwenputer repo.

## Design direction and build stack

**Look.** Technical, calm, monochrome with one accent. Think a well-set research README, not a crypto landing page: no gradients, no glow, no mascots, no stock 3D. Large type for the three claims, small type for everything else, generous whitespace. Diagrams are flat SVG with the same two colours as the text (ink and accent) and read correctly in light and dark.

- Typeface: one humanist sans for body and headings (Inter or similar, self-hosted), one monospace for code and token streams (JetBrains Mono or similar). No third font.
- Colour: near-black ink on off-white, inverted for dark mode; one accent used only for the "circuit-owned" segment in Figure B, status chips and links. Tokens defined once in CSS variables.
- Motion: none, except a single optional animation on Figure B where the circuit segment fills in left to right once on scroll. Respect `prefers-reduced-motion`.
- Every figure has alt text that states its point in one sentence.

**Stack.** Static site, no backend, no CMS.

- Astro (latest) with plain `.astro` components and Markdown for the long pages; Tailwind is optional, plain CSS is fine. No React unless a component needs state, and none does in v1.
- Figures are hand-written SVG files in `src/figures/`, inlined at build so they inherit CSS variables for theming.
- `public/status.json` drives the phase chips on `/build` and `/roadmap`: `{"phase1":"in progress","phase2":"planned",…,"backbone":"qwen3.5-4b","updated":"YYYY-MM-DD"}`. The build agent (Muse) updates this file; the site reads it at build time and also fetches it at runtime so a redeploy is not needed for a status change.
- Evidence numbers live in `src/data/evidence.json` with `value`, `caption`, `source_url`, `measured_on` fields. Copy never hard-codes a number; it reads from here. Bracketed placeholders in this spec map to keys in that file.
- Deploy: Cloudflare Pages or Vercel from the `main` branch; preview deploys on PRs. Lighthouse ≥ 95 on all four scores on mobile.
- Analytics: none in v1. If added later, a cookie-free tool (Plausible or similar) so the site stays banner-free.

**Repo layout.**

```
ethereus-site/
  src/pages/        index.astro, how-it-works.md, roadmap.astro, build.astro, credits.md
  src/components/   Nav, Footer, Claim, EvidenceStrip, StatusChip, Figure
  src/figures/      fig-a-row-is-a-byte.svg, fig-b-who-writes.svg, fig-c-roadmap.svg
  src/data/         evidence.json, credits.json
  public/           status.json, og.png, favicon
  README.md         how to update evidence.json and status.json; how to add a figure
```

**Responsive.** Three breakpoints: phone (single column, figures scroll horizontally if wider than the viewport), tablet, desktop (max content width \~72ch for prose, wider for figures). The three-claims block stacks on phone.

**Accessibility.** Semantic headings in order, visible focus states, colour contrast ≥ 4.5:1, every link's text makes sense out of context, no text in images.

## Assets, claims policy, credits, legal

**Claims policy.** The site is read by people who will check. Rules:

- A number appears only with a source link and a `measured_on` date in `evidence.json`. Qwenputer's numbers (230k replayed positions, KL 6e-4, 3,700 instr/s) are cited to its README and `rv32.md`; Ethereus's numbers come from `docs/eth-bench.md` in the build repo.
- Until a phase passes its gate, its claims are written as design intent ("is designed to", "will") and its evidence slot shows "not yet measured". No placeholder numbers ship.
- "Unforgeable" and "cannot" are used only for the position-ownership property, which is a mechanical fact of the bridge. Everything else is "exact on N cases" or "0 of N".
- Nothing on the site claims audit status, security certification, or suitability for custody of real funds. Footer disclaimer: *Research software. Not audited. Do not use with keys that control funds you cannot lose.*
- No token, no "community", no Discord link in v1.

**Credits page content** (`credits.json`, rendered as a list with links):

| Work | Author | What Ethereus takes from it | License |
| --- | --- | --- | --- |
| [Qwenputer](https://github.com/divagr18/qwenputer) | Divyansh Agrawal | The compiler, the RISC-V CPU, the bridge, the filesystem, every mechanism number | MIT (code), Apache-2.0 (weights) |
| [nesformer](https://github.com/dbreunig/nesformer) | Drew Breunig | The standalone tiny-model development pattern and the host-side split | MIT |
| [ethskills](https://github.com/austintgriffith/ethskills) and [evm-audit-skills](https://github.com/austintgriffith/evm-audit-skills) | Austin Griffith | The knowledge base loaded into the filesystem | check repo license before launch |
| Qwen3.5-4B | Qwen team | The backbone | Apache-2.0 |
| [Kimi Linear](https://arxiv.org/abs/2510.26692), [Kimi K3](https://arxiv.org/abs/2607.24653) | Moonshot AI | The roadmap's target architecture | papers cited; weights not used in v1 |

Authors are named exactly as they name themselves in their repos. No photos of any person on the site.

**Assets to produce.** Three SVG figures (A, B, C), an OG image (1200 × 630, the headline and Figure B at small size), a favicon (a single highlighted row from Figure A), and a plain-text logotype "Ethereus" in the body typeface. No illustrations beyond these. Do not reuse images from the Qwenputer repo; redraw.

**Name and domain.** Working name *Ethereus*. Check the GitHub handle and a `.dev` or `.xyz` domain before launch; if taken, the name changes and the spec is updated, nothing else does.

**Legal.** Site content CC BY 4.0; code in the site repo MIT. Link both licenses in the footer.

## Acceptance criteria and open questions

**Done when all of these hold:**

- [ ] Five routes build and deploy; preview URL shared.
- [ ] Home page copy matches this spec section for section; the three claims and the hero are verbatim.
- [ ] Figures A, B, C exist as themed SVG, pass a visual check in light and dark, and have one-sentence alt text each.
- [ ] Every number on the site resolves to an `evidence.json` entry with `source_url` and `measured_on`; placeholder slots render "not yet measured", never a bracket or a fake number.
- [ ] `status.json` is fetched at runtime and the chips change without a redeploy (tested by editing the file).
- [ ] Lighthouse ≥ 95 on performance, accessibility, best practices, SEO, on mobile.
- [ ] Footer carries the research-software disclaimer and both licenses.
- [ ] Credits page names all five works with working links.
- [ ] A fresh reader (someone not on the project) reads the home page and can say in one sentence what Ethereus guarantees and why. Run this once before launch.

**Working rules for the site agent.**

- The build spec doc is the technical source of truth; when in doubt about a mechanism statement, quote it or ask, do not improvise.
- Numbers come from `docs/eth-bench.md` in the build repo and from the Qwenputer README; the site agent does not measure anything itself.
- Report back at: skeleton deployed; copy and figures in; evidence and status wired; pre-launch check. Each report is a preview link plus a list of deviations from this spec, one line each.

**Open questions** (decide before the skeleton):

- [ ] Final name and domain.
- [ ] Whether `/build` shows the hackathon track (dates, rules) or stays a pure "run the code" page in v1.
- [ ] Whether Phase 3's policy gate ships before or after the site launches; if after, the guardrail claim ships as design intent with its evidence slot empty.

Source docs: *Ethereus — Build Spec for Agents* (this workspace); [Qwenputer](https://github.com/divagr18/qwenputer); [nesformer](https://github.com/dbreunig/nesformer); [ethskills](https://github.com/austintgriffith/ethskills); [Kimi K3 report](https://arxiv.org/abs/2607.24653); [Kimi Linear](https://arxiv.org/abs/2510.26692).
