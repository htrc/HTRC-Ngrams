# Language-Specific Tokenization Limitations

This document tracks known tokenization artifacts, limitations, and edge cases inherent to the Stanza models used in this pipeline. These are expected behaviors of the underlying NLP models and are documented here so researchers are aware of them when analyzing the bigram exports.

## Korean (`kor`)

### Number/Classifier Agglutination
In Korean, Stanza frequently groups numbers and their subsequent classifiers (counter words) into a single token rather than splitting them.
- **Example:** `2권` (2 books) is tokenized as `["2권"]` instead of `["2", "권"]`.
- **Impact:** Researchers querying for exact numeric tokens (e.g., `2`) in Korean texts may miss occurrences where the number is attached to a classifier. This is a standard artifact of Korean agglutinative morphology handling in neural NLP models.

## French (`fra`)

### Elision Handling (MWT)
In French, Stanza's Multi-Word Token (MWT) expansion successfully splits elided words (e.g., *l'arbre*), but the apostrophe remains attached to the article.
- **Example:** `L'arbre` (The tree) is tokenized as `["L'", "arbre"]` instead of `["L", "'", "arbre"]` or `["La", "arbre"]`.
- **Impact:** Researchers analyzing articles and stopword distributions must be aware that elided articles (like `l'`, `d'`, `qu'`) exist as distinct token forms with trailing apostrophes in the final bigram output.

## Norwegian (`nor`)

### Generic Norwegian Uses the Bokmål Model

Stanza maps the generic Norwegian code `no` to its Bokmål resources (`nb`).

- **Impact:** Volumes specifically written in Nynorsk may not receive the most appropriate tokenizer when their metadata identifies only generic Norwegian.
- **Handling:** A future metadata rule can route an explicit Nynorsk code to Stanza's `nn` model without changing generic Norwegian processing.

## Vietnamese (`vie`)

### Multi-Syllable Tokens

Stanza can represent a Vietnamese word made of multiple written syllables as one token containing spaces.

- **Example:** `Thư viện` may appear as one token rather than two separate tokens.
- **Impact:** Spaces inside `w1` or `w2` are part of the token. TSV columns remain separated by tabs.
