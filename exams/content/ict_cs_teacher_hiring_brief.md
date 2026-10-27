# ICT / Computer Science Teacher — Hiring Assessment Brief

Internal source for an original English-language assessment. This is a broad professional screening test, not a Cambridge examination or a claim of certification. It tests whether a candidate can reason accurately about computing and explain it safely and clearly to learners. The hiring team should use the result alongside a teaching demonstration and interview.

## Fixed blueprint

- **Time:** 90 minutes. **Total:** 60 whole-number marks. **Questions:** 20, all compulsory.
- **Slots 1–10:** four-option MCQ, one mark each. **Slots 11–20:** open response, five marks each. Keep the position, domain, skill, point value and approximate difficulty fixed across candidates; vary only the self-contained data and context.
- Candidate-facing stems, options, code comments, table headings, model answers, rubrics, and explanations must be in clear English. Use original material. Do not reproduce past-paper questions or mention Cambridge in the questions.
- The candidate may answer open questions with concise reasoning, pseudocode, Python 3, SQL, or a formula as the stem requires. State all assumptions in the stem. Do not rely on vendor-specific software, internet access, installed packages, or knowledge of a particular school's systems.
- Assess understanding and professional judgement, not memorisation of obscure syntax or product names. Each open question must have **five independent one-mark criteria** and a worked model answer. Give credit for equivalent correct reasoning. Avoid criteria that award the same idea twice.
- For each MCQ, create four plausible, distinct choices, exactly one correct answer, one one-mark criterion, and an explanation of why the correct option works. Avoid “all/none of the above”, trick wording, and options that differ only by punctuation.

### Code and data quality rules

Use Python 3 for code analysis. Every snippet must be complete, 6–14 lines, use only built-in types/functions, and have deterministic behaviour. Prefer integers, strings, lists, dictionaries, `range`, simple functions, and explicit inputs. No randomness, network/filesystem access, threads, third-party modules, mutable default arguments, implementation-dependent behaviour, or floating-point display traps. Keep snippets readable in plain text. When asking for output, specify the exact call or `print` statement; the model answer must give the exact output. When asking for a bug, include exactly one intentional logic defect and an unambiguous specification. Provide a failing case and a passing boundary case in the model answer. Code should be safe to execute locally during review.

For SQL and spreadsheet questions, include a tiny synthetic dataset or precise schema and state the intended SQL dialect or spreadsheet reference convention if it matters. Never use real student names, marks, email addresses, credentials, or school records.

## Question specifications

### Slot 1 — Data representation · MCQ, 1 mark

Interpret a small binary/hex value, bit width, or overflow in a concrete device context. One calculation or inference; no rote definition alone.

### Slot 2 — Boolean logic · MCQ, 1 mark

Reason from a two-input gate, a short expression, or a miniature truth table. Give all input values needed.

### Slot 3 — Computer systems · MCQ, 1 mark

Distinguish CPU, RAM, persistent storage, or an OS responsibility in a realistic classroom-device symptom.

### Slot 4 — Networking · MCQ, 1 mark

Choose the best explanation of DNS, IP addressing, routing, or client/server behaviour from a concrete failure. Avoid brand-specific commands.

### Slot 5 — Cybersecurity · MCQ, 1 mark

Choose a proportionate control for phishing, account compromise, access rights, or a lost device. Distinguish confidentiality from availability where useful.

### Slot 6 — Databases · MCQ, 1 mark

Infer a primary/foreign key relationship, uniqueness rule, or why a simple query duplicates rows. Use a tiny schema.

### Slot 7 — Programming · MCQ, 1 mark

Trace one short Python 3 conditional/loop or identify a valid boundary test. Do not use a syntax gotcha.

### Slot 8 — Spreadsheets · MCQ, 1 mark

Reason about relative versus absolute references, a formula copied down, or data validation. Specify cells and values.

### Slot 9 — Responsible digital practice · MCQ, 1 mark

Choose the safest action involving source credibility, copyright/licensing, accessibility, or learner data. No jurisdiction-specific legal trivia.

### Slot 10 — Algorithms and testing · MCQ, 1 mark

Compare two simple strategies, select an edge case, or identify a precondition/invariant. Use small inputs rather than asymptotic notation alone.

### Open responses: five marks each

### Slot 11 — Python trace and explain · open, 5 marks

Give a short function with a loop, list/dictionary update, and an explicit call. Ask for its exact returned/printed value and a compact trace of the state changes. The five one-mark criteria must cover: identifying the initial state; a first meaningful iteration; a later iteration or branch; the exact final value/output; and a correct explanation of why that value results. Set data so a plausible superficial reading gives a different answer.

### Slot 12 — Python debug and test · open, 5 marks

Show a function that almost meets a simple specification but has one off-by-one, wrong comparison, or misplaced return defect. Ask the candidate to locate the defect, demonstrate one failing input, make the smallest correction, and propose a boundary test. Award one mark each for: precise fault location; actual behaviour on the failing case; required behaviour; valid correction; and a useful boundary test with expected result. Avoid a syntax error and avoid multiple independent defects.

### Slot 13 — Python code review and improvement · open, 5 marks

Show a correct but unnecessarily costly or fragile small routine, for example duplicate detection or counting in a list. Ask for a clearer or more efficient approach that preserves a stated contract, plus a test. Award one mark each for: correct current behaviour/contract; identified inefficiency or fragility; valid revised algorithm or Python code; explanation of why the revision improves the specific issue; and a test covering an edge case. Do not require a named library or a single preferred implementation.

### Slot 14 — Algorithm design · open, 5 marks

Give a small authentic school-independent problem, such as selecting the earliest valid booking from unsorted records or finding a repeated identifier. Ask for an algorithm/pseudocode and reasoning. Award one mark each for: correct input/output contract; main processing steps; handling empty or no-match input; handling a tie/duplicate or other meaningful edge case; and a justified time or space trade-off. A sound simple solution may earn full marks even if another solution is faster.

### Slot 15 — Data representation and logic · open, 5 marks

Give a compact scenario combining a fixed-width binary value or simple Boolean condition with a practical consequence. Ask the candidate to compute/interpret the result and explain a limitation or correction. Award one mark each for: correct representation/calculation; correct interpretation of width/condition; recognition of the relevant boundary/overflow or Boolean case; valid corrective choice; and clear reasoning tied to the scenario. Supply all conventions, including signed versus unsigned, if relevant.

### Slot 16 — Networks and security triage · open, 5 marks

Provide a plausible school-network symptom with two or three observations, such as a device reaching a local service but not a named external site, plus a privacy or account-security constraint. Ask for a diagnostic sequence and proportionate response. Award one mark each for: a relevant first check; a second discriminating check; a reasoned likely cause based on the evidence; an appropriate mitigation or escalation; and a student-data/security safeguard. Do not reward indiscriminate disabling of security controls.

### Slot 17 — SQL and data modelling · open, 5 marks

Give two tiny related tables with explicit columns, keys, and 4–6 synthetic rows. Ask for a standard SQL query that answers a concrete question, plus a brief explanation of join/aggregation behaviour. Award one mark each for: correct join key; required filtering; correct grouping or projection; handling of duplicate/empty cases as specified; and explanation of the expected result from the given data. Provide the expected result in the model answer, but never in the candidate stem.

### Slot 18 — Spreadsheet/ICT reasoning · open, 5 marks

Show a tiny synthetic spreadsheet with headers, values, and a formula intended to be filled down. Ask the candidate to identify a formula/reference error, provide a correct formula, predict one output, and suggest a validation or accessibility improvement. Award one mark each for: identifying the specific reference/error; correct formula with absolute/relative references as needed; correct computed output; appropriate data validation or check; and a concrete accessibility or usability improvement. Do not depend on one vendor's menu labels.

### Slot 19 — Teaching a misconception · open, 5 marks

A 14–17-year-old learner has a specific misconception about a computing concept, such as believing that a variable “remembers” every previous value or that encryption and hashing are interchangeable. Ask for a 10-minute teaching response. Award one mark each for: accurately diagnosing the misconception; a correct, age-appropriate explanation or analogy; an active learner task; a formative check that distinguishes the misconception from understanding; and feedback or a next step based on the check. Reward clarity and evidence of learning, not theatrical style.

### Slot 20 — Inclusive, safe assessment design · open, 5 marks

Present a realistic class where learners differ in device access, language proficiency, or accessibility needs, and a proposed digital tool would collect identifiable work or use generative AI. Ask the candidate to adapt one CS/ICT learning activity and assess it fairly while protecting student data. Award one mark each for: an explicit learning objective; an accessible/offline or low-bandwidth alternative that preserves the objective; a fair evidence/assessment method; a specific privacy or safeguarding measure; and an actionable feedback/iteration step. Do not ask for jurisdiction-specific legal citations.

## Difficulty and review gates

- Target a capable teacher, not a senior software engineer: roughly one-third foundational knowledge, one-third application/code reasoning, and one-third evaluation or teaching judgement. Questions 11–13 must require genuine code analysis, but fit within the 90-minute total.
- Each question must be answerable from information in its stem plus general ICT/CS knowledge. Do not require access to a specific syllabus, software licence, command line, or personal teaching history.
- Keep the same skill and five-mark rubric for a slot across generated variants. Vary numbers, names of synthetic objects, and surface context without changing the number of reasoning steps or introducing new topics.
- Before releasing a variant, a human reviewer must execute every Python snippet and verify the exact output, bug, fix, and edge tests; independently run/check SQL and spreadsheet calculations; inspect all MCQ options for a single defensible key; and check that each five-mark rubric has distinct, observable evidence.
- Review the wording for ambiguity, accessibility, bias, accidental student data, and answer leakage. Confirm the full set covers both ICT and Computer Science as well as teaching judgement. Publish only after this review.

## Curriculum references (scope guidance only)

- Cambridge IGCSE Computer Science 0478 syllabus, 2026–2028: https://www.cambridgeinternational.org/Images/697167-2026-2028-syllabus.pdf
- Cambridge International AS & A Level Computer Science 9618 syllabus, 2027–2029: https://www.cambridgeinternational.org/Images/721397-2027-2029-syllabus.pdf
- Cambridge IGCSE ICT 0417 syllabus, 2026–2028: https://www.cambridgeinternational.org/Images/697139-2026-2028-syllabus.pdf
- ISTE Standards for Educators: https://iste.org/standards/educators
- ISTE Computational Thinking Competencies: https://iste.org/standards/computational-thinking-competencies
