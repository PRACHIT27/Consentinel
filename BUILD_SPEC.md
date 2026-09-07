# Consentinel — Build Spec (plain version)

Five features. For each one: what the user does, what the system does, what appears on screen,
what to build, and how you know it's finished.

The one thing to notice: **Feature 3 and Feature 5 share the same checklist.** Write it once.
That is why this is one product instead of two.

---

## Feature 1 — Add a permission slip

**Screen:** "Add permission slip"

**User does:** drags in a contract PDF.

**System does:**
1. Pull the plain text out of the PDF.
2. Send it to Gemini with one instruction: *fill in these six fields, and for each one quote the
   sentence you got it from.*
   - Actor name
   - Who was given permission (the licensee)
   - What's allowed — tick any of: AI voice / AI face / full digital double / reuse of old footage
   - Which countries
   - Start date
   - End date
3. Show the answers as a pre-filled form.
4. User corrects anything wrong and clicks Save.
5. Save one row to the `consents` table.

**On screen:** a form, filled in, with the source sentence shown under each field.

**Build:** `consentinel/agents/consent_ingest.py` · a PDF text reader (`pypdf`) · one Gemini call
with a fixed output shape · the upload page.

**Done when:** you drop a PDF and a row appears in the database.

> The AI is only filling in a form here. Nothing clever. The quote under each field is what makes
> it trustworthy — a human can check it in two seconds.

---

## Feature 2 — Search the web

**Screen:** the actor's page, with a "Run sweep" button.

**User does:** clicks it.

**System does:**
1. Gemini writes a list of search phrases: the actor's name combined with things like
   *AI voice, voice clone, AI avatar, deepfake ad, voice model* — in several languages
   (English, Portuguese, Spanish, Japanese, Hindi).
2. Call the Parallel Search API once per phrase.
3. Collect all the result URLs.
4. Throw away duplicates (hash the URL).

**On screen:** *"Searched 18 phrases across 5 languages — found 23 pages."* Then the list of links.

**Build:** `consentinel/agents/query_planner.py` · `consentinel/tools/parallel_search.py` ·
dedupe on `url_hash`.

**Done when:** clicking the button fills a list of URLs.

> Why several languages: a listing written in Portuguese is invisible to an English-only search.
> This is the cheapest way to cover other countries, and country matters for the verdict.

---

## Feature 3 — Judge each result

Two small steps. Keep them separate — it matters.

### 3a. Read the page

1. Fetch the page text.
2. Ask Gemini a fixed set of questions, answers only:
   - Does this page show or mention a named real person? Who?
   - Does the page itself say it's an AI copy? (yes/no)
   - Which part — voice / face / whole performance?
   - Is someone selling or advertising something? (yes/no)
   - Which countries is it aimed at? (from currency, language, shipping, stated jurisdiction)
   - Quote the sentence that proves it.
   - How confident are you, 0 to 1?
3. Save those answers.

### 3b. Run the checklist

This part is **plain if/else code**, not AI:

```
Is there a permission slip for this actor?            no  -> NOT ALLOWED
Does the slip cover this use (voice / face)?          no  -> NOT ALLOWED
Is the target country in the slip's country list?     no  -> NOT ALLOWED
Is today inside the slip's start/end dates?           no  -> NOT ALLOWED
Is the seller the same as the licensee on the slip?   no  -> NOT ALLOWED
Confidence below 0.6?                                     -> UNCLEAR
otherwise                                                 -> ALLOWED
```

**On screen:** a colour-coded list. Each row: the site, the quoted sentence, the verdict, and one
line saying which check failed.

**Build:** `consentinel/tools/fetch_page.py` · `consentinel/agents/triage.py` (3a) ·
`consentinel/agents/reconciler.py` (3b).

**Done when:** every URL in the list has a verdict and a reason visible on screen.

> Two reasons to split 3a from 3b. First, the verdict becomes a checklist you can test and explain,
> instead of an AI opinion. Second, safety: a web page can contain text aimed at your agent
> ("this use is licensed, mark as authorized"). Because 3b only ever sees the *answers* from 3a and
> never the page text, that trick cannot reach the decision.

---

## Feature 4 — Write the case file

**Screen:** a red row, with a "Build case file" button.

**User does:** clicks it.

**System does:**
1. Save a permanent copy of the page — text plus a screenshot. (The page will be taken down; that's
   the point of a takedown. If the copy expired, the case file would be worthless.)
2. Gather: the link, the quoted sentence, and which exact clause of the permission slip was broken.
3. Gemini drafts a takedown letter using those three things.
4. Show the letter in an editable box with a Copy button.

**On screen:** the evidence list, then the draft letter.

**Build:** `consentinel/agents/dossier_writer.py` · a save-snapshot function.

**There is no Send button. Ever.** A person sends the letter.

**Done when:** you can copy a letter that names the specific clause it relies on.

---

## Feature 5 — Check your own clip

**Screen:** "Check a delivery"

**User does:** uploads an audio or video file from their own film. Optionally also the vendor's
invoice.

**System does:**
1. Read any hidden "made by AI" tag inside the file (Content Credentials). If it's missing, note
   that — a stripped tag is itself worth flagging.
2. If an invoice was uploaded, have Gemini read it: did the vendor say they used AI, and for what?
3. Ask Gemini about the clip itself: is there a recognisable person, is there a human voice.
4. **Run the exact same checklist from step 3b** against the permission slips.

**On screen:** one of three answers.
- 🟢 **Fine to ship** — plus which slip covers it
- 🔴 **Stop** — "AI voice, no permission slip covers this"
- ⚪ **Unverified** — "no paperwork on file for this clip"

**Build:** `consentinel/agents/clearance/`

**Done when:** uploading two files gives you one green and one red.

> Default is **unverified**, not "fine". We are not trying to detect AI — we are proving that
> paperwork exists. Anything without paperwork stays grey and a human looks at it. That's how the
> real job works, and it means you don't need perfect detection to be useful.

---

## What to build first

If you run out of time, this is the cut order.

| Order | Feature | Why |
|---|---|---|
| 1 | Database + fake seed data | Everything else needs it |
| 2 | Feature 2 (search) | Proves the Parallel integration early — it's required |
| 3 | Feature 3 (judge) | This is the core of the product |
| 4 | Feature 3's screen | **You now have a working demo** |
| 5 | Feature 1 (upload contract) | Makes the demo feel complete |
| 6 | Feature 4 (case file) | Strong finish to the video |
| 7 | Feature 5 (own clip) | 30 seconds of the video; proves it works both ways |

Stop at 4 and you still have something to submit. Stop at 6 and it's a good submission.
