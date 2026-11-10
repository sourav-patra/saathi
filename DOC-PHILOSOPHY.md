# Documentation philosophy

This file sets how documents in this repository are written and organised. It applies to READMEs, specs, plans, design records, handovers, runbooks and agent instruction files.

## What a document is for

A document exists so a reader can act. A new reader should finish the entry page able to say what the project does, who it is for, what goes in, what comes out and what decision the output supports.

Every other document earns its place by serving one task that reader has, or by being a reference the entry page points to.

## Before writing

Ask, in order, and stop at the first yes: does this document need to exist; does an existing document already cover it; can a pointer to that document replace it; can it be one line in an existing file. Only then write a new file.

Read the code, config and existing records the document describes before describing them; a document that restates what a config file or a help command already says goes stale and is deleted. Leave out persona names (including the owner's) and references to the owner's system paths, and any similar private detail

When a rule or template changes because of a measurement, write the prediction and the pass mark before the run, compare against writing with no template, and confirm on cases that were not used to choose the change. This applies to the record of that measurement, not to each document written under the rule.

## Four kinds

Every file is one kind, and the kind sets its folder, its name and its shape.

| Kind | Purpose | Shape |
|---|---|---|
| Contract | A rule or design the project is held to: spec, design record, policy, this file | Goal, requirements with an ID, a check and an evidence location, out of scope, order of work |
| Record | What happened and what is true now: handover, state note, lessons | One dated line per fact; under 16 KB |
| Receipt | Proof a check ran: logs, hashes, acceptance rows | Machine format with one plain-text line at the top saying what it proves |
| Message | A request or answer to one recipient | ID, kind, sender, recipient, the one question or action, evidence locations |

## Entry pages

The public README is the entry page: a stranger decides within a minute whether to try the project. Rules 1 to 5 apply to the README. A product page keeps the same purpose, evidence and accuracy in its own layout.

The repository sidebar follows rule 6. Writing rule 1 lets the README open with its promise; writing rule 7 lets its "skipped or not checked" line live in a linked limits page. Every other writing rule still applies.

1. Order: the name and a one-line promise of what the reader gets; status badges from the hosting platform; a figure of the project working; why this project, as the reader's problem in plain words with one line of evidence; key features as short bullets; a quick start before any reference detail, each step run and ending on a check; links to the detail.
2. Short: no paragraph over 60 words, most under 25. Lists and tables carry detail. Explain a technical term in plain words at first use, or move it to a linked page.
3. Plain: no filler words, no "etc.", no dashes joining clauses, no "not X but Y", no sales adjectives. Each sentence states one thing the reader can check.
4. Visual: at least two distinct figures answering different questions, usually the project working and how it works, in one consistent style with labels, alt text and captions. Colour supports meaning but never carries it alone. The working figure comes before the first section; use an animation when motion explains the task. Badges, logos, repeated images and image links inside code do not count. Show rendered figures, never diagram source or ASCII boxes. The checks in "Visual explanations" apply.
5. Keep what is true: move results, method, detailed limits, troubleshooting and repository maps to linked pages when they interrupt the reader. Keep status, the key evidence and the main limits next to the claims they qualify. Correct stale or unsupported claims and remove what public-export rules forbid.
6. Sidebar: an accurate description, a working website or documentation link, 5 to 10 relevant topics, a social preview image, and a release for each published version. An unreleased project says so. Never create placeholder releases or links to pass the check.
7. Check: the entry-page lint passes and a reviewer checks the rules it cannot. Then an unfamiliar reader, without coaching, says within a minute who it is for, what problem it solves and its status; after reading it, says the input, output, supported decision, first action, key evidence and main limits. Record the answers and misunderstandings, fix, and repeat failed checks. This adds to the figure review and the overhaul's reader checks.

## Names and the index

Files are named `<KIND>-<subject>-<YYYY-MM-DD>[-r<n>].md`: kind in capitals, subject in lowercase with hyphens, ISO date only, a revision suffix only when the file replaces an earlier one. The replaced file moves to `archive/` the same day.

`00-INDEX.md` at the project root lists the current file of each kind with its path and a hash prefix; a file not in the index is not current, and a reader starts at the index.

## Writing rules

1. The first line is what the reader does next, or the one fact that decides it.
2. One meaning lives in one place; everywhere else points to it.
3. Multi-step work is numbered, one bounded action per step, the fewest steps that work, each ending on a check the reader can run.
4. Numbers are measured or marked not measured, with the command that would measure them.
5. Claims name their source, and the text keeps observed, reported, proposed and unmeasured distinct. A citation shows where a claim came from. It does not show the claim is true: check that the cited text says what the sentence says, and say when truth was not checked.
6. Plain technical language: named actors, terms defined at first use, errors stated as cause and fix, no openers that announce the content, no closers that recap it, no emphasis formatting, no figurative phrasing.
7. Every document ends with one line naming what was skipped or not checked.
8. Secrets, tokens, personal data and paths outside the project never appear.
9. Size limits: record 16 KB, contract 24 KB, index 2 KB. Over the limit means split or archive, with a summary at the top of the kept file.
10. Files read by automated agents put steps before reference, push reference the agent needs only sometimes behind a pointer, and state the wanted behaviour rather than the forbidden one.
11. A template labels each section required or optional, and requires only the sections every document needs. A document leaves out an optional section its subject does not touch; its length follows the subject, never the section count.

## Visual explanations

Check whether a visual would make the reader's task easier before completing an explanatory document. Include a diagram when prose makes relationships, movement, boundaries, branching, or change hard to follow.

Use a table for attributes, a plot for measured quantities, and a sentence when that is sufficient. There is no minimum figure count, except on entry pages.

Each figure answers one reader question and sits beside the explanation that uses it. Use the same names as the text, meaningful alternative text, and a caption explaining the conclusion and limits. Label planned behavior and omitted paths.

Keep views small enough to follow; move detail into a separate view when necessary. Labels and shapes must carry meaning without relying on color alone.

For documentation diagrams, maintain a plain-text SVG in the repository as the sole editable source and edit it directly.

Keep third-party diagram authoring tool names and tool-specific source, metadata, configuration, installation steps, and commands out of repository files, commit messages, and pull request and issue text. A tool the project genuinely depends on at runtime or in its build is exempt.

Maintain no parallel diagram-language, HTML, or external regeneration source. Use system fonts and no external font or resource requests. If the reading surface requires PNG, render it from the maintained SVG and update the derived image whenever the SVG changes.

Readers must be able to view the figure without authoring tools or external scripts.

Check depicted facts against the relevant source revision, then inspect the rendered result at its intended size for readable text, correct arrows, clear boundaries, clipping, overlap, and contrast. Record what ran and what was visually inspected; an unperformed check stays pending.

For figures on entry pages or primary task paths, ask a reader who did not author the document to explain the depicted relationships and limits without coaching; record their answer and any misunderstanding before revising the figure.

For other figures, a reader check is recommended; record it as not run when it is omitted. An overhaul follows its agreed reader acceptance check. Illustrations do not establish measured benefit or replace the checks supporting a claim.

## Checks

A lint runs before every commit and fails on: a filename date in any other format, spaces in a filename, dashes joining clauses in prose, filler and promotional words, a file over its size limit, a missing or stale index entry, a record line that does not match the record shape.

A check counts as evidence only after it has been shown to fail on a deliberate fault. Record that fault next to the passing result.

The lint supplements a human edit for clarity; it does not replace it. A document change ships with the same evidence as a code change: the check that ran and its result.

Checksum: [DOC-PHILOSOPHY.md.sha256](DOC-PHILOSOPHY.md.sha256). The sidecar records the SHA-256 of this complete file.

Skipped or not checked: nothing in this file is project-specific; each project supplies its own index and lint configuration.
