# Evidence register — reference products

Rules: every row is one of **DOCUMENTED** (vendor's own public page states it), **OBSERVED**
(seen executing — only possible where a workflow is publicly reachable), **INFERRED** (our
conclusion, labelled), or **UNKNOWN**. Vendor marketing claims are recorded as DOCUMENTED claims,
never as verified capability. No login, licence or access control was bypassed.

## MecAgent (mecagent.com)

Investigated 2026-10-14 with REA 6.4.0 (`rea-agents`, MIT) on the Windows PC plus an isolated
CDP-driven Chrome profile. Full record, 23 evidence IDs and SHA-256s:
`C:\New folder (4)\reverse_engineering\MECAGENT_EVIDENCE_INDEX.json`, summary in
`FINDINGS_BRIEF.md`, feature table in `MECAGENT_FEATURE_MATRIX.md`. Not repeated here.

Key boundary: the desktop installer is behind `/login` (`GET /download/solidworks` → 302), so no
binary was obtained and no runtime or native analysis of the product exists.

## Pre6 AI (pre6.ai) — first investigation, 2026-10-17

Method: public pages fetched with the session's web reader (no login, no scripts executed, no
forms submitted). REA was **not** run against pre6.ai in this session: REA lives on the Windows
PC, and the cloud workspace's shell cannot reach pre6.ai (egress allowlist). A REA
`inspect_web_page` + `capture_browser_scenario` pass from the PC is listed as an open task.

| ID | Page | Class | Claim (verbatim or close paraphrase) |
|---|---|---|---|
| P6-01 | `/` | DOCUMENTED | Four products: **Costing**, **Lens**, **Machinist**, **PPAP**. "zero install \| zero training \| zero complexity". "SOC 2 Type 2 certified". |
| P6-02 | `/` | DOCUMENTED | Meta description says "Revolutionary intelligence platform coming soon" while the page shows products and customer logos — availability per product is UNKNOWN. |
| P6-03 | `/lens/` | DOCUMENTED | "Fully automated ballooning and report generation from engineering drawings"; "Fast (5 seconds)"; "Reads GD&T"; scanned files; "near 100% accuracy on complex, scanned drawings" — **no dataset or method published**. |
| P6-04 | `/lens/` | DOCUMENTED | OCR claimed on blurry text, hand sketches, old photographs, rotated dimensions. |
| P6-05 | `/lens/` | DOCUMENTED | AS9102 FAIR generation; customisable output layout; auto-identification of critical parameters; "ensures every dimension is accounted for before export". |
| P6-06 | `/lens/` | DOCUMENTED | SPC agents on shop-floor data (moving-range chart example, alerting), instrument/gauge library with calibration status. |
| P6-07 | `/machinist/` | DOCUMENTED | STEP upload → 3-axis feature recognition → tolerances/threads pulled from 2D drawing → toolpaths and tool selection → machine-specific G-code → G-code analysis/compare. No controller, strategy or simulation named. |
| P6-08 | `/costing/` | DOCUMENTED | STEP + drawing upload; agents for die, alloy, methoding, machining, quality, cost; rate book (machine hour rates, labour, overhead, metal/resin prices, scrap, margin) with sourced, overridable defaults; revision matching by drawing number/revision/title block; HPDC deepest; Excel & PDF export; $20 per RFQ. |
| P6-09 | `/costing/` | DOCUMENTED | Fill/solidification simulation, clamp force, undercut flags, NADCA tolerance standards for machining. |
| P6-10 | `/ppap/` | DOCUMENTED | Chat-run pipeline: read PFD → map drawings → coverage check (pauses for approval: "2 of 10 dimensions need a method") → PFMEA from approved library → Control Plan → fill customer template (submission.xlsx). "Every number traces back to its source." Approve/reject change cards. |
| P6-11 | all | UNKNOWN | Input file formats for drawings, accuracy methodology, API/integrations, CAD integrations, how ambiguous annotations are handled, model providers, data residency. |
| P6-12 | — | INFERRED | Pre6's centre of gravity is drawing → inspection/quality documents (Lens, PPAP) and quoting; it presents **no native CAD-application automation**. MecAgent is the inverse (SolidWorks automation, no inspection documents). ASTraCAD spans both. |

Sources: https://pre6.ai/ , https://pre6.ai/lens/ , https://pre6.ai/machinist/ ,
https://pre6.ai/costing/ , https://pre6.ai/ppap/ (fetched 2026-10-17).

## Pre6 AI — second pass from the PC, 2026-10-17 (browser observation)

Method: the Claude desktop app's built-in browser (a real Chromium pane) on the Windows PC —
page text, accessibility tree, the Resource Timing list and the network log were read. **REA was
not used for this pass** (the earlier plan named REA; this is an equivalent passive observation,
recorded as such). Boundaries kept: no login, no account created, no form submitted, no file
uploaded, analytics cookies **declined** both times they were offered, no consent given. Where a
step needed one of those, the pass stopped and the boundary is the finding.

| ID | Page | Class | Finding |
|---|---|---|---|
| P6-13 | `/` | OBSERVED | The public site is static: page load fetched only `pre6.ai` assets, `static.cloudflareinsights.com` and `fonts.gstatic.com`; **no API or data request** was made. The home page preloads four separate front-end bundles, `/costing/_app`, `/lens/_app`, `/machinist/_app`, `/ppap/_app` (SvelteKit `_app/immutable` layout, `__sveltekit` globals present). Served through Cloudflare (`/cdn-cgi/challenge-platform`, `/cdn-cgi/rum`). |
| P6-14 | `/lens/` | OBSERVED | The Lens page now carries **Pricing**, **User Manual**, **Contact Sales**, **Login**, **Try For Free** and **See an Example** — none of which the first (2026-10-17 morning) fetch recorded. |
| P6-15 | `/lens/` | OBSERVED | **"See an Example" is gated on accepting analytics cookies**: clicking it opens "Cookie Preferences — Allow cookies to continue using Lens"; "Decline Analytics" returns to the landing page, and a second click re-opens the gate (seen twice). The example was therefore **not** viewed; accepting would have been consent given on the user's behalf. |
| P6-16 | `/lens/` → Login | OBSERVED | Login redirects to `https://auth.pre6.ai/login?r=https://pre6.ai/lens/app`: "Sign in with Google" or **Work Email + OTP**. The product app is `/lens/app` behind that login. Nothing was entered. "Try For Free" leads to account creation and was not followed. |
| P6-17 | `/lens/manual/getting-started` | DOCUMENTED | **Accepted inputs: PDF, JPG, JPEG, PNG, TIFF**, "no file size limit". **No DXF/DWG or CAD-model input is listed.** Scanning is OCR ("OCR in progress"); balloons "within seconds"; a progress bar counts verified dimensions ("21/24 verified"). Resolves part of P6-11. |
| P6-18 | `/lens/manual/dimensions/list` | DOCUMENTED | Two dimension states only: **Verified** (green) and **Needs verification** (orange, "the AI wasn't confident or it's in Scanning state"). Verification is a per-dimension AI confidence state plus user confirmation; **no geometry cross-check is described**. Users can add missed dimensions and edit values (Count, Measurement Instrument, Notes, Characteristic Designator). |
| P6-19 | `/lens/manual/tolerances/scanning` | DOCUMENTED | General tolerances come from **scanning the drawing's tolerance table** (drag a box, "Scan for tolerances") into an editable "Custom Scanned Standard", or from a selected standard; matched dimensions highlight. ISO 286 fit-class resolution is not mentioned. |
| P6-20 | `/lens/manual/exporting/inspection-sheet` | DOCUMENTED | Excel inspection plan in Pre6's format or a **user-uploaded Excel template with column mapping**; scope All / by designator (Critical, Major, Minor) / by group; columns include serial no. + **grid location**, type, designator, count, upper/lower tolerance, checking method, measurement fields, part-information form, OK/Not-OK formula. PDF of the ballooned drawing. Manual also lists redaction of sensitive areas, reference grid, rotation, view detection. |
| P6-21 | `/lens/pricing` | DOCUMENTED | "SME Plus — ₹1,00,000 per year": 20,000 balloons/year, up to 2 users, auto ballooning, smart redaction, PDF & Excel export, OCR, inspection reports. (One tier was present in the page's article text.) |
| P6-22 | — | INFERRED | Against ASTraCAD Inspect: Lens covers **raster/scanned and PDF** drawings (ASTraCAD: DXF + vector-PDF text, no OCR) and ships custom Excel templates, grid references, redaction and designators that ASTraCAD lacks. ASTraCAD reads **DXF dimension entities and cross-checks each callout against drawn geometry**, resolves ISO 286 fits from the published table, and re-opens its own exports to verify them — none of which the Lens manual describes. Its "near 100% accuracy" claim (P6-03) remains unpublished; parity is **not** demonstrated in either direction because neither product was run on a shared drawing set. |

P6-11 remains UNKNOWN for accuracy method, API/integrations, model providers and data residency.

## Pre6 Lens — public example run, 2026-10-17 (user-approved cookie acceptance)

The user approved accepting Lens's analytics cookies for **one** observation pass of the public
example (resolves the P6-15 boundary). Same built-in browser; Pre6's **own sample drawing**
only — nothing of ours was uploaded, no login, no form, no download, no value edited. The
acceptance persists in the built-in browser's profile until its cookies are cleared.

| ID | Where | Class | Finding |
|---|---|---|---|
| P6-23 | `/lens/` → See an Example | OBSERVED | Accepting cookies opens "How it works": Pre6's sample drawing ("Enclosure", dwg 10001125, A3, 3-view + sections + two iso renders) shown "Before Processing", with a **Process Drawing** button. "Processing your drawing… usually takes just a few seconds", then the full workspace opens at **`/lens/try`** without login. |
| P6-24 | `/lens/try` network | OBSERVED | Sequence: `GET public.pre6.ai/images/pre6_lens_enclosure.pdf` → `POST /lens/api/upload?example=true` (52 KB JSON, **0.54 s**) → `POST /lens/api/ocr/titleblock` (1.1 s) → **11 × `POST /lens/api/ocr/batch`** (0.2–0.7 s each). Workspace usable ~10 s after the click. |
| P6-25 | upload response | OBSERVED | Record has `parsedBy: "lens-backend"`, `userId: null`, `orgId: null`, an anonymous `tid: "$device:<uuid>"`, and the page as a **4096 × 2897 raster** (`width/height`, webp thumbnails) — the vector PDF is processed as an **image** (OCR route), not as vector text. |
| P6-26 | `/lens/try` UI | OBSERVED | **73 dimensions, 7 views detected, "56/73 verified"**, an "Inspection Plan 86" counter, tabs Dims / Views / Tols, unit set/convert, Download PDF, Inspection Sheet export, toolbar (box select, notes, edit, contrast, reorder, rotate, hide). Of the first 39 rows rendered, 11 were orange (needs verification) and 28 green (verified). |
| P6-27 | row #17 | OBSERVED | A **GD&T feature control frame** (position ⌀0.01 \| A \| B \| C) is ballooned and listed as a characteristic, rendered as symbols, marked verified. **Lens reads GD&T frames on this sample** (P6-03 claim observed in one instance); ASTraCAD does not read FCFs. |
| P6-28 | rows #14, #22 | OBSERVED | Stacked limit dimensions are listed as **two bare numbers** — "86.98 87.05", "47.95 48.1" — and marked **verified** (green). Whether they become min/max limits in the export was not observed (export not downloaded). The same token on ASTraCAD's synthetic plate ("86.98 87.05" as loose text) is **escalated, not accepted**, by design. |
| P6-29 | rows #7, #9, #26, #33 | OBSERVED | Deviation tolerances read as "53 +0.01 -0.02", "80.129 +0.015 -0.025", "⌀36 +0.25 -0.1", "⌀15 +0.25 -0.15" — all **orange (needs verification)**; plain values and ± forms were mostly green. Verification state is the system's confidence, not a geometry check (consistent with P6-18). |
| P6-30 | `/lens/try` third parties | OBSERVED | After acceptance the page contacts **Google Ads / DoubleClick (conversion id 17385937004), Google Analytics (G-W8G8B69KBW), Google Tag Manager and Mixpanel** in addition to Cloudflare. Before acceptance (P6-13) only Cloudflare analytics and Google Fonts were seen. |
| P6-31 | — | INFERRED | 73 dimensions on an A3 multi-view sheet, GD&T read, in ~10 s, mostly marked verified: on **raster/scanned-style input Lens is clearly ahead** of ASTraCAD, which has no OCR. Its "verified" state can mark a two-number token as verified without a stated separator (P6-28), where ASTraCAD refuses to guess. **Not a measured accuracy comparison**: no ground truth exists for this sample, and ASTraCAD cannot read it (raster route absent). A fair benchmark needs the same drawings run through both, with labels — the M2 corpus. |
