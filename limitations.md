# Data Quality and Language Limitations

This document is the single language-by-language record of known data-quality
limitations for the HTRC N-grams pipeline. It describes observed behavior and
candidate responses; it does not define automatic correction rules.

## Review Scope

The core 20 languages were reviewed on 2026-09-16 with the current
`tokenize_page()` path and Stanza 1.11.0 resources installed for that review.
The controlled cases covered punctuation, case, numbers, apostrophes, inline
and line-break hyphens, clitics, diacritics, and historical spelling where
relevant.

The evidence levels used below are:

- **Current controlled behavior:** reproduced with the current code and the
  tokenizer resources installed for the review.
- **Legacy volume observation:** seen in earlier HTRC volume exports produced
  before the Stanza migration. These examples remain useful as evidence about
  source OCR, but not as proof of current token boundaries.
- **Current volume review pending:** representative HTRC volumes still need to
  be generated with the current pipeline before making corpus-wide claims.

English has also been inspected in current-generation red-team TSVs. Legacy
volume outputs were available for English, German, French, Spanish, Chinese,
Russian, Italian, and Portuguese. The remaining core languages currently have
controlled-tokenization evidence only. This distinction prevents a small or
older sample from being treated as a corpus-wide result.

## Pipeline-Wide Interpretation Notes

- Language is assigned once per volume. Quoted or embedded languages are a
  known limitation, not a tokenization defect.
- Case, punctuation, and numbers are retained as tokens. The pipeline does not
  lemmatize words or modernize historical spelling.
- Inline hyphens become punctuation tokens. Line-final hyphens followed by a
  newline are removed so the two word fragments are recombined.
- The pipeline removes selected scanner boilerplate, superscript and subscript
  characters, and zero-width characters. It does not apply hardcoded Fraktur,
  long-s, or spelling corrections.
- Alphabetic-looking OCR errors can therefore survive as tokens. Correction
  maps should not be added from isolated examples; recurrence and ambiguity
  must first be measured across representative volumes.
- Header and footer removal uses only sequential plain-text lines, not page
  coordinates. OCR column ordering can make those heuristics unreliable.
- The Stanza tokenization path does not apply a single-character whitelist.
  Current Stanza tokens, including valid and erroneous single-character tokens,
  are retained unless a later filter removes their bigrams.

## English (`eng`)

### Contractions, Possessives, and Hyphens

- **Current behavior:** `Can't Sam's well-known co-author re-enter?` becomes
  `Ca`, `n't`, `Sam`, `'s`, `well`, `-`, `known`, `co`, `-`, `author`, `re`,
  `-`, `enter`, `?`.
- **User impact:** searches for the surface forms `can't`, `Sam's`, or
  `well-known` will not find a single matching token. Hyphen punctuation is
  included in adjacent bigrams.
- **Recommended handling:** retain and document the Stanza output because it is
  deliberate and consistent with the project decision to treat punctuation as
  tokens.
- **Risk of changing it:** recombining selected forms would require
  language-specific rules and would make current exports incompatible with
  earlier runs.
- **Status:** verified current behavior. Current English red-team TSVs were
  also reviewed.

## German (`deu`)

### Historical Spelling and Fraktur OCR

- **Current behavior:** historical forms such as `Daß`, `muß`, and `Fluß` are
  preserved. `OCR-Fehler` becomes `OCR`, `-`, `Fehler`.
- **Legacy observation:** older volume samples contained Fraktur-like OCR
  substitutions. Those source errors are no longer corrected by hardcoded
  mappings.
- **User impact:** historical and modern forms have separate counts, while OCR
  mistakes can introduce additional false forms.
- **Recommended handling:** preserve historically valid spelling. Record
  recurring OCR errors for review, but add a dictionary correction only when
  the source form is frequent and unambiguous.
- **Risk of changing it:** broad substitutions such as `f` to `s` or `b` to
  `d` would corrupt legitimate German words.
- **Status:** controlled behavior verified; current Stanza volume review is
  pending.

## French (`fra`)

### Elisions and Inversion Hyphens

- **Current behavior:** `L'arbre qu'on voit n'est-il pas d'Artagnan?` becomes
  `L'`, `arbre`, `qu'`, `on`, `voit`, `n'`, `est`, `-`, `il`, `pas`, `d'`,
  `Artagnan`, `?`.
- **User impact:** elided forms such as `L'` and `d'` are tokens with trailing
  apostrophes. Inversion forms produce hyphen punctuation bigrams.
- **Recommended handling:** preserve this Stanza MWT behavior and explain it in
  query documentation.
- **Risk of changing it:** removing apostrophes would merge distinct written
  forms and reduce alignment with the tokenizer model.
- **Status:** verified current behavior; current Stanza volume review pending.

## Spanish (`spa`)

### Line-Break Hyphens and Historical OCR

- **Current behavior:** `La pre-\ngunta continúa.` becomes `La`, `pregunta`,
  `continúa`, `.`. Inverted punctuation and quotation marks remain tokens.
- **Legacy observation:** older scans contained alphabetic OCR fragments and
  language-mixed material. Volume-level language policy does not remove quoted
  or embedded languages.
- **User impact:** true line-break word fragments are repaired, but
  alphabetic-looking OCR errors remain searchable forms.
- **Recommended handling:** retain line-break repair and avoid broad long-s or
  spelling modernization rules.
- **Risk of changing it:** historical spellings and valid multilingual content
  could be removed as noise.
- **Status:** controlled behavior verified; current Stanza volume review
  pending.

## Chinese (`zho`)

### Historical and Traditional-Term Segmentation

- **Current behavior:** `图书馆有2本书。監察院編印古籍。` becomes `图书`, `馆`,
  `有`, `2`, `本`, `书`, `。`, `監察`, `院`, `編`, `印古`, `籍`, `。`.
- **User impact:** concepts such as `監察院` may not exist as one token, so a
  researcher searching only for the unsplit term can miss relevant counts.
- **Recommended handling:** leave the general model unchanged for now. A
  reviewed historical lexicon may be loaded later if evaluation on real
  volumes shows repeated, research-relevant segmentation failures.
- **Risk of changing it:** a custom lexicon can force one interpretation of an
  ambiguous sequence and bias other genres or periods.
- **Status:** verified current behavior and legacy volume observation; current
  Stanza volume review pending.

### Structural Lines

- **Current behavior:** the Chinese profile has no language-specific header
  keywords. Generic numeric-line checks still run, but ordinary Chinese
  contents or running-header text is not identified by keyword.
- **Recommended handling:** evaluate multi-character candidates such as
  contents or appendix labels on representative volumes before adding them.
  Avoid single-character markers such as `第` or `卷`.
- **Risk of changing it:** common structural words can also occur in valid
  prose, and plain-text OCR line order is not a reliable page coordinate.
- **Status:** confirmed configuration limitation.

## Japanese (`jpn`)

### Compound Segmentation

- **Current behavior:** `図書館には本が2冊あります。明治期の史料です。` includes
  `図書`, `館`, `明治`, `期`, and `史料` as separate tokens.
- **User impact:** some compounds and period expressions are distributed across
  multiple bigrams rather than represented as one term.
- **Recommended handling:** retain the model output until representative
  historical-volume review identifies stable domain terms that justify a
  lexicon.
- **Risk of changing it:** forced compounds can hide valid internal word
  relationships and may not generalize across writing styles.
- **Status:** verified current controlled behavior; current volume review
  pending.

### Structural Lines

- **Current behavior:** the Japanese profile has no language-specific header
  keywords, so ordinary textual running headers can remain.
- **Recommended handling:** test multi-character header candidates on real
  pages before adding them.
- **Risk of changing it:** words such as `序文` or `索引` can appear in valid
  discussion, while OCR line order can misidentify page margins.
- **Status:** confirmed configuration limitation.

## Russian (`rus`)

### Pre-1918 Orthography

- **Current behavior:** `Въ`, `старомъ`, `текстѣ`, and `дѣло` remain distinct
  from modern `В`, `тексте`, and `дело`.
- **User impact:** longitudinal queries must include historical variants when
  the research question spans the spelling reform.
- **Recommended handling:** preserve these forms because they are historical
  language, not OCR junk. Any modernized view should be an optional derived
  dataset rather than a destructive replacement.
- **Risk of changing it:** automatic modernization would erase evidence used
  in linguistic and historical research.
- **Status:** verified current behavior; this is a scope note, not a defect.

## Italian (`ita`)

### Apostrophe Elisions

- **Current behavior:** `L'uomo dov'è? C'era un'altra storia.` becomes `L'`,
  `uomo`, `dov'`, `è`, `?`, `C'`, `era`, `un'`, `altra`, `storia`, `.`.
- **User impact:** elided prefixes remain visible with apostrophes, while their
  following words remain independently queryable.
- **Recommended handling:** retain and document the Stanza behavior.
- **Risk of changing it:** stripping apostrophes or joining tokens would alter
  genuine Italian grammar and token adjacency.
- **Status:** verified current behavior; legacy OCR examples exist, but current
  Stanza volume review is pending.

## Portuguese (`por`)

### Historical Apostrophes and Spelling

- **Current behavior:** in `D'água e n'um livro; ação e pharmacia.`, `D'água`
  and `n'um` remain whole tokens, while `pharmacia` is preserved.
- **User impact:** Portuguese apostrophe behavior differs from French and
  Italian, and historical spellings have counts separate from modern forms.
- **Recommended handling:** document the language-specific tokenizer output and
  preserve historical spellings. Evaluate apostrophe forms on more periods
  before proposing overrides.
- **Risk of changing it:** forced splitting or modernization could destroy
  historically meaningful forms and introduce inconsistent rules.
- **Status:** verified current behavior; legacy long-s and OCR examples were
  observed, but current Stanza volume review is pending.

## Arabic (`ara`)

### Clitics and Vocalized Text

- **Current behavior:** `وبالمدرسة` expands to `و`, `ب`, `المدرسة`. In a fully
  vocalized example, `وَبِالْمَدْرَسَةِ كِتَابٌ.`, Stanza produced
  `وَبِالْمَدْرَسَة`, `ِ`, `كِتَابٌ`, `.`; a combining mark became a standalone
  token.
- **User impact:** clitic bigrams reflect expanded grammatical components, but
  vocalized material can produce mark-only tokens or distinct token forms.
- **Recommended handling:** measure mark-only tokens on representative Arabic
  volumes. Consider normalization only if the project explicitly decides that
  vowel marks are not part of the research product.
- **Risk of changing it:** removing Arabic diacritics would discard meaningful
  orthographic and linguistic information.
- **Status:** verified current controlled behavior; current volume review
  pending.

### Structural Lines

- **Current behavior:** the Arabic profile has no language-specific header
  keywords.
- **Recommended handling:** evaluate multi-word or multi-character structural
  labels on real volumes before adding any line rule.
- **Risk of changing it:** short Arabic header words can also be ordinary prose,
  and RTL OCR ordering can make first-line and last-line assumptions fragile.
- **Status:** confirmed configuration limitation.

## Latin (`lat`)

### Enclitics and Orthographic Variation

- **Current behavior:** `librosque` remains one token; Stanza does not split the
  enclitic `-que` in the reviewed case. Case and historical orthographic forms
  are preserved.
- **User impact:** searches for a base form such as `libros` do not automatically
  include `librosque`. No lemmatized counts are exported.
- **Recommended handling:** preserve surface tokens and document that queries
  may require inflected and enclitic variants.
- **Risk of changing it:** rule-based suffix splitting can mis-handle words
  whose endings only resemble enclitics.
- **Status:** verified current controlled behavior; current volume review
  pending.

## Dutch (`dut`)

### Apostrophe Forms

- **Current behavior:** `'t Is een goed boek; 's morgens leest men.` retains
  `'t` and `'s` as complete tokens.
- **User impact:** Dutch apostrophe forms differ from French and Italian MWT
  output and must be queried in their retained form.
- **Recommended handling:** preserve and document the language-specific output.
- **Risk of changing it:** a global apostrophe rule would make correct behavior
  in one language incorrect in another.
- **Status:** verified current controlled behavior; current volume review
  pending.

## Polish (`pol`)

### Single-Character Words and Diacritics

- **Current behavior:** `W bibliotece są 2 książki, a w domu jedna.` preserves
  `W`, `a`, and `w` as tokens, together with Polish diacritics.
- **User impact:** valid one-character prepositions and conjunctions remain in
  bigrams; case-sensitive `W` and `w` are separate unigram forms.
- **Recommended handling:** retain the Stanza output. Do not reintroduce a
  general single-character deletion rule.
- **Risk of changing it:** deleting one-character tokens would remove frequent
  grammatical words.
- **Status:** verified current controlled behavior; current volume review
  pending.

## Swedish (`swe`)

### Case-Sensitive Single-Character Words

- **Current behavior:** `Å, vad bra! I biblioteket finns 2 böcker.` retains `Å`
  and `I` as tokens and preserves punctuation and diacritics.
- **User impact:** case-sensitive single-character forms have separate counts,
  consistent with the export policy.
- **Recommended handling:** retain the tokenizer output and evaluate OCR noise
  through frequency and context rather than character length alone.
- **Risk of changing it:** a single-character filter would remove valid Swedish
  words.
- **Status:** verified current controlled behavior; current volume review
  pending.

## Hebrew (`heb`)

### Niqqud Removal and Prefix Expansion

- **Current behavior:** Hebrew vowel and cantillation marks are removed before
  Stanza. `הַסֵּפֶר בַּסִּפְרִיָּה.` produced `ה`, `ספר`, `ב`, `ספרי`, `ה`, `.`.
- **User impact:** vocalized and unvocalized spellings are combined, while
  grammatical prefixes can appear as separate tokens.
- **Recommended handling:** document this intentional normalization and prefix
  behavior.
- **Risk of changing it:** retaining niqqud would create separate surface forms
  and could change Stanza segmentation; removing it loses vocalization evidence.
- **Status:** verified current controlled behavior.

### Hebrew Punctuation Preservation

- **Current behavior:** preprocessing removes Hebrew vowel and cantillation
  marks while preserving punctuation such as maqaf (`־`, U+05BE), paseq (`׀`,
  U+05C0), and sof pasuq (`׃`, U+05C3). Hebrew punctuation is separated before
  Stanza so it can be represented as independent tokens.
- **User impact:** punctuation can participate in exported bigrams, and words
  separated by Hebrew punctuation are no longer fused during preprocessing.
- **Historical compatibility:** exports created before this correction may have
  different Hebrew token and bigram counts because the earlier broad Unicode
  range also removed punctuation.
- **Status:** corrected language-specific preprocessing behavior; representative
  Hebrew output should still be reviewed before production release.

## Korean (`kor`)

### Eojeol-Level Tokens and Number/Classifiers

- **Current behavior:** `도서관에는 책이 2권 있습니다. 서울대학교 자료입니다.`
  becomes `도서관에는`, `책이`, `2권`, `있습니다`, `.`, `서울대학교`,
  `자료입니다`, `.`.
- **User impact:** particles and endings remain attached at the reviewed
  tokenizer level, and a number plus classifier such as `2권` is one token.
  Researchers querying only for `2` or `권` will miss that occurrence.
- **Recommended handling:** retain and document the model output. A morpheme-
  level Korean product would be a different dataset design, not a small cleanup
  rule.
- **Risk of changing it:** ad hoc splitting can damage lexicalized forms and
  create behavior inconsistent with Stanza.
- **Status:** verified current controlled behavior; current volume review
  pending.

## Czech (`cze`)

### Single-Character Prepositions

- **Current behavior:** `V knihovně jsou 2 knihy, a k domu jde.` preserves `V`,
  `a`, and `k`, as well as Czech diacritics.
- **User impact:** valid short grammatical tokens remain available, with case
  preserved.
- **Recommended handling:** retain Stanza output and avoid character-length
  deletion.
- **Risk of changing it:** a generic one-character cleanup would remove valid
  Czech grammar.
- **Status:** verified current controlled behavior; current volume review
  pending.

## Danish (`dan`)

### Single-Character Words and Diacritics

- **Current behavior:** `I biblioteket er der 2 bøger, og Å står alene.`
  preserves `I`, `Å`, `bøger`, punctuation, and the number.
- **User impact:** case and Danish letters remain distinct in the export.
- **Recommended handling:** retain current behavior and use context rather than
  token length when evaluating OCR noise.
- **Risk of changing it:** a broad character rule could remove valid words or
  fold meaningful orthographic distinctions.
- **Status:** verified current controlled behavior; current volume review
  pending.

## Indonesian (`ind`)

### Hyphenated Reduplication

- **Current behavior:** `buku-buku` becomes `buku`, `-`, `buku` because the
  pipeline deliberately detaches inline hyphens before Stanza.
- **User impact:** Indonesian reduplication is represented through punctuation
  bigrams rather than one surface token.
- **Recommended handling:** retain this behavior while the project-wide inline
  hyphen policy remains in force, and document the query pattern.
- **Risk of changing it:** a language-specific recombination would improve some
  reduplications but create inconsistent hyphen semantics across languages and
  ambiguous compounds.
- **Status:** verified current controlled behavior; current volume review
  pending.

## Hungarian (`hun`)

### Agglutinative Surface Forms

- **Current behavior:** `A könyvtárban 2 könyv van, és az előszó rövid.` keeps
  `könyvtárban` as one inflected surface token and preserves Hungarian
  diacritics.
- **User impact:** counts for a lexical base are distributed across inflected
  forms because the export is tokenized but not lemmatized or morphologically
  segmented.
- **Recommended handling:** retain surface forms. Morphological normalization
  would require a separately defined research product.
- **Risk of changing it:** stemming or segmentation can collapse meaningful
  grammatical distinctions and introduce model-dependent errors.
- **Status:** verified current controlled behavior; current volume review
  pending.

## Additional Configured-Language Notes

The pipeline currently supports seven languages beyond the core 20. Their
environment and tokenizer initialization are validated, but they are outside
the present volume-level data-quality review.

### Norwegian (`nor`)

Generic Norwegian code `no` resolves to Stanza's Bokmål resources (`nb`). A
volume written in Nynorsk may therefore receive a less appropriate tokenizer
when its metadata supplies only generic Norwegian. An explicit Nynorsk metadata
code could be routed separately in a future reviewed change.

### Vietnamese (`vie`)

Stanza can represent a Vietnamese word made of multiple written syllables as
one token containing spaces. For example, `Thư viện` may appear as one token.
Spaces inside `w1` or `w2` are token content; TSV fields remain separated by
tabs.

Hindi, Croatian, Turkish, Thai, and Urdu have validated Stanza initialization,
but representative data-quality review remains pending.

## Release Follow-Up

Before declaring the volume-level review complete, generate representative
current-pipeline outputs for every core language, including at least one older
or visibly OCR-damaged volume where available. Review frequent and singleton
bigrams, punctuation tokens, structural lines, scanner boilerplate, and failure
diagnostics. New correction rules should require repeated evidence across
multiple volumes and should record both expected benefit and false-positive
risk.
