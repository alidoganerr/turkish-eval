# turkish-eval

54 Turkish prompts for testing language models, plus a script that validates the set and grades answers by rule.

Models that write decent Turkish still trip on the same small things: `kalplar` instead of `kalpler`, `ISTANBUL` with a dotless I, `bir bin`, `Evde ki`, a `mi` glued to the verb. General benchmarks rarely look at these, so most of this set is about them. There are also some short factual questions, word problems, instruction-following tasks, translation and register (formal vs. casual).

51 of the prompts have automatic checks. The other three (two idioms and one about negation) need someone to read the answer.

## Running it

Python 3.9+, nothing else.

```
python3 turkish_eval.py check prompts.jsonl
python3 turkish_eval.py stats prompts.jsonl
python3 turkish_eval.py autograde prompts.jsonl responses.jsonl
python3 turkish_eval.py export prompts.jsonl responses.jsonl dataset.jsonl
```

`responses.jsonl` is whatever you collected from the model, one line per prompt:

```json
{"id": "ek-cogul", "response": "kalpler, saatler, alkoller, roller, harfler"}
```

`check` validates the prompt file and also runs every reference answer through its own checks. `stats` counts prompts per category and difficulty. `autograde` prints each failure and a pass count per category, and exits with 1 if anything failed. `export` writes prompts, responses and the reviewer checklists in the format [rubric-eval](https://github.com/alidoganerr/rubric-eval) reads (`id`, `prompt`, `response`, `criteria`), for the items that need a person or if you want a second opinion on all of them.

## Prompt format

One JSON object per line:

```json
{"id": "ek-iyelik",
 "category": "ek_uyumu",
 "difficulty": 3,
 "prompt": "Burun, ağız, omuz ve şehir sözcüklerine 3. tekil kişi iyelik eki gelince nasıl yazılır?",
 "reference": "burnu, ağzı, omzu, şehri",
 "criteria": [{"id": "unlu-dusmesi", "text": "Burun, ağız ve omuzda ünlü düşmesini uygular (burnu, ağzı, omzu)", "weight": 4}],
 "auto": [{"kind": "contains", "value": "burnu"}]}
```

`criteria` is the checklist for a human reviewer; a negative weight marks a flaw. `auto` is optional, and every check in it has to pass.

| kind | passes when |
|---|---|
| `contains`, `not_contains` | the text is (is not) in the response |
| `regex`, `not_regex` | the pattern is (is not) found anywhere in the response |
| `fullmatch` | the whole response, trimmed, matches the pattern |
| `order` | the strings appear in this order |
| `sentences` | the response has exactly this many sentences |
| `max_words` | the response has at most this many words |
| `json_equals` | the response is JSON equal to the value (a code fence around it is fine) |
| `line_initials` | the non-empty lines start with these letters, compared with Turkish upper-casing |

Responses are compared in NFC, and curly apostrophes count as `'`.

## Caveats

The automatic checks are plain string and pattern matching. An answer that contains `kalpler` passes that check even if the rest is wrong, and a correct answer worded unexpectedly can fail a `contains`. Look at the failures before believing the number. They're good for catching the typical mistakes and for making runs repeatable, not for ranking models; 54 prompts is far too few for that.

The sentence counter is rough. It knows that `15.00` isn't the end of a sentence but it doesn't know abbreviations like `vb.`.

The numeric, date and alphabet-order answers are computed in code, and the tests recompute them a second way. The references also go through their own checks, which is a cheap way to catch a wrong one.

The file also has `tr_upper` and `tr_lower` (i to İ, ı to I, without leaving a stray combining dot), `normalize`, `count_sentences` and `count_words` if you need them elsewhere.

Tests: `python3 -m unittest`

Ali Doğaner. MIT licensed.
