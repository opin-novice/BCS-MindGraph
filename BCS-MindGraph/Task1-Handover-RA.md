# Task 1 — হস্তান্তর নথি (Handover)

**Task 1: Prepare the BCS corpus and temporal metadata**

| | |
|---|---|
| **Temporal model** | **Model B — গৃহীত ও বাস্তবায়িত** |
| Holdout cutoff (t\*) | `2023-04-19` |
| Release corpus (Model B seed) | **69** fact (63 static + 6 dynamic) |
| **Strict temporal guard** | **PASS ✅ — 69/69** |
| উৎস corpus | 616 fact — guard **FAIL** (সঠিক, প্রত্যাশিত) |
| **Temporal class coverage** | **616 = 312 static + 274 dynamic + 30 review** (আগে ছিল 248 / 233 / **135**) |
| **Pilot evidence** | ৪৭টি fact source করা → ২১টি candidate → পড়ার পর **১টি পূর্ণ সমর্থন** |
| Task 2 শুরু করা যাবে? | মূল criterion পূরণ; তবে নিচের governance কাজ বাকি |
| **Task 1 অবস্থা** | ✅ **COMPLETE** — PI sign-off 2026-09-17 |
| **খোলা PI-সিদ্ধান্ত** | **১টি** (Q263) — `model_b_workflow/pi_adjudication_queue.jsonl` |
| **Task 2 data path** | ✅ যাচাইকৃত — generation বাকি (HF key নেই) |
| সর্বশেষ আপডেট | 2026-09-17 |

> **অবস্থা সংক্ষেপে:** Task 1-এর মূল acceptance criterion — *2023-04-19-এ strict
> temporal guard pass* — **অর্জিত হয়েছে**। যে 69টি fact দিয়ে holdout চলবে, সেগুলোর
> প্রত্যেকটির temporal প্রমাণ যাচাই করা। বাকি আছে **সম্প্রসারণ ও governance**
> (pilot, triage, external review) — এগুলো guard আটকাচ্ছে না, কিন্তু corpus-এর
> আকার ও review-এর মান বাড়াতে দরকার।

> এই নথির উদ্দেশ্য: Task 1-এ এ পর্যন্ত কী কী হয়েছে, কেন হয়েছে, এবং Task 1 সম্পূর্ণ করতে আর কী কী বাকি — যাতে আপনি কাজ ধরে এগোতে পারেন এবং PI-কে Task 2-এর জন্য প্রস্তুত করতে পারেন।

---

## ১. Task 1 আসলে কী চায়

প্রতিটি fact-এ evidence দিয়ে সমর্থিত temporal metadata বসাতে হবে:

`valid_from`, `valid_to`, `source_published_at`, `source_tier`, `relation`, `observed_at`

**কঠোর নিয়ম (এগুলো ভাঙা যাবে না):**

1. কোনো তারিখ **অনুমান বা আবিষ্কার** করা যাবে না
2. `fact_text`-এ থাকা সাল = `source_published_at` **নয়**
3. Source যাচাই না হলে field ফাঁকা রেখে review-তে পাঠাতে হবে
4. **45তম BCS (target paper)** কখনো evidence হিসেবে ব্যবহার করা যাবে না
5. Cutoff-এর পরে প্রকাশিত কোনো evidence holdout corpus-এ যাবে না
6. কোনো fact মোছা যাবে না, কোনো ID বদলানো যাবে না
7. অসমর্থিত source **চুপচাপ বদলে** দেওয়া যাবে না

---

## ২. সবচেয়ে বড় আবিষ্কার — এটা আগে বুঝুন

Corpus-এর **citation layer কাজ করে না**। এটাই Task 1 আটকে থাকার একমাত্র মূল কারণ।

616টি fact-এর ১৬৪টি আলাদা `source_url` একটা একটা করে fetch করে দেখা হয়েছে:

| ফলাফল | সংখ্যা |
|---|---:|
| HTTP 404 (পাতা নেই) | 92 |
| DNS / TLS / connection failure | 46 |
| 403 / 401 / 500 / 202 | 7 |
| HTTP 200 (সফল) | 19 |
| — এর মধ্যে catch-all/soft-404 | 4 |

**১৬৪টির মধ্যে ১৪৫টি URL কোনো document-ই ফেরত দেয় না।**

491টি corpus fact `https://bpsc.gov.bd/exam/43th-bcs` ধাঁচের URL cite করে। BPSC-র সাইটে এই path নেই — লক্ষ করুন বানানও ভুল (`43th`, সঠিক হতো `43rd`), যা machine-generated URL-এর স্পষ্ট চিহ্ন। এগুলো **synthetic placeholder**, বাস্তব citation নয়।

**একটি cited page-ও cutoff-এর আগের কোনো publication date দেখায় না।** তাই মূল corpus থেকে একটিও `valid_from` প্রমাণসহ বসানো যায়নি।

### Leakage (আলাদা সমস্যা, URL কাজ করুক বা না করুক)

| সমস্যা | সংখ্যা | নিয়ম |
|---|---:|---|
| 45তম BCS (target paper) cite করে | 22 | Rule 9 |
| Cutoff-পরবর্তী BCS (46তম–50তম) cite করে | 102 | Rule 10 |

---

## ৩. যা যা সম্পন্ন হয়েছে

### ৩.১ যাচাই-অবকাঠামো — `verify_temporal_corpus.py`

পুনরুৎপাদনযোগ্য (reproducible) tool, ৫টি subcommand:

```bash
python verify_temporal_corpus.py fetch      # প্রতিটি URL fetch, evidence cache
python verify_temporal_corpus.py verify     # 40টি করে batch, প্রতি batch-এ validation
python verify_temporal_corpus.py validate   # schema / date / ID / provenance gate
python verify_temporal_corpus.py audit      # coverage audit তৈরি
python verify_temporal_corpus.py guard      # pipeline-এর strict guard offline dry-run
```

- ৪০টি করে ১৬টি batch, প্রতি batch-এর পর validation + checkpoint
- কোনো batch fail করলে পরেরটায় যায় না
- Wayback Machine থেকে cutoff-পূর্ব archived capture খোঁজে

### ৩.২ ফলাফল (মূল corpus)

| Metric | মান |
|---|---:|
| মোট fact | 616 |
| প্রমাণসহ `valid_from` | **0** |
| প্রমাণসহ `valid_to` | **0** |
| `source_published_at` | 14 (১১টি দুর্বল HTTP header, cutoff-পরবর্তী) |
| `source_tier` বসানো | 616 |
| `relation` বসানো | 616 |
| Review প্রয়োজন | **616** |
| Cutoff-এ যোগ্য | **0** |

### ৩.৩ যেসব ভুল ধরা পড়ে সংশোধন হয়েছে

**(ক) `web_scraper.py`-তে tier bug** — `str.lstrip("www.")` prefix নয়, **character set** কাটে। ফলে `worldbank.org` → `orldbank.org`, `who.int` → `ho.int` হয়ে যেত এবং নিজেদের tier map-এ মিলত না। ৩টি জায়গায় ঠিক করা হয়েছে, সাথে subdomain matching (যাতে `notbbs.gov.bd.evil.com` tier-1 না পায়)। ১২/১২ test case pass।

**(খ) ভুল `source_tier`** — আগের pass একটা ad-hoc table দিয়ে `*.gov.bd` → tier 1 বসিয়েছিল, ফলে ৪৯১টি **অকার্যকর** BPSC URL সর্বোচ্চ authority পেয়েছিল। প্রকল্পের নিজস্ব policy-তে `bpsc.gov.bd` mapped নয় → এখন tier 4।

**(গ) ভুয়া `source_snapshot_hash`** — আগে ছিল `sha256("url|fact_text")`, অর্থাৎ corpus row-এর hash, যা source সম্পর্কে **কিছুই প্রমাণ করে না**। এখন প্রকৃতপক্ষে download করা content-এর digest; কিছু না নামলে `null`।

### ৩.৪ Triage (Step 1)

| শ্রেণি | সংখ্যা |
|---|---:|
| Holdout-eligible | **596** |
| বাদ — 45তম BCS selection leakage | **18** |
| বাদ — placeholder/junk row | **2** |
| `static` | 248 → **312** |
| `dynamic` | 233 → **274** |
| `unclassified` | 135 → **30** (§৩.৯ দেখুন) |

> **১৮টি বাদ দেওয়ার কারণটা গুরুত্বপূর্ণ:** এই fact-গুলোর একমাত্র exam attribution 45তম BCS। অর্থাৎ answer key দেখে এগুলো *বাছাই* করা হয়েছে। পরে ভালো source দিলেও এই দূষণ যায় না — কারণ corpus-এর গঠনই target paper দেখে প্রভাবিত।

### ৩.৫ Relation coverage উন্নয়ন

`STATED_AS` মানে "এই দাবিটা কী ধরনের, জানি না" — এতে structured date বের করা অসম্ভব।

Corpus-এ আগে থেকেই থাকা curated metadata (`_subtopic`, `_question_type`) ব্যবহার করে:

**`STATED_AS`: 434 → 177** (70% → 29%), specific relation: 182 → 439

### ৩.৬ Re-sourcing pipeline — `resource_corpus.py`

**মূল কৌশল:** ২০২৬ সালের live web দিয়ে ২০২৩ সালের দাবি যাচাই করা যায় না। তাই **MediaWiki revision API** — cutoff-এর দিনে article ঠিক যেমন ছিল, তেমনটাই আনা হয়।

এক call-এ তিনটি জিনিস পাওয়া যায়:
1. Cutoff-এর সময়কার article text (দূষণমুক্ত)
2. Revision timestamp — প্রমাণিতভাবে cutoff-এর আগে → `source_published_at`
3. স্থায়ী `oldid` permalink → `source_url` + content hash

**৫৯৬টি fact-এর পূর্ণ pass-এর ফল:**

| | সংখ্যা | % |
|---|---:|---:|
| Cutoff-পূর্ব source পাওয়া গেছে | **181** | 30% |
| তার মধ্যে `valid_from` সহ | **41** | 7% |
| — infobox field থেকে | 19 | |
| — prose proximity থেকে | 22 | |
| কোনো source মেলেনি | 415 | 70% |

সব revision date `2014-10-07` – `2023-04-19`-এর মধ্যে, **প্রতিটি cutoff-এর আগে**।

> **গুরুত্বপূর্ণ:** এই ফলাফল `resourcing_proposals.json`-এ আছে — **corpus বদলানো হয়নি** (Rule 13)। প্রতিটি প্রস্তাব PI-এর accept/reject-এর অপেক্ষায়।

**Topic-ভেদে বিশাল পার্থক্য:**

| Topic | n | sourced | dated |
|---|---:|---:|---:|
| International Relations / Defense / Geography | 27 | 87–100% | মিশ্র |
| History | 97 | 43% | 11% |
| Liberation War | 74 | 20% | 3% |
| **Economy** | **140** | **16%** | **0%** |
| **Constitution** | **50** | **10%** | **2%** |

Economy-তে 0% dated **ভুল নয়** — ওগুলো dynamic fact, tool ইচ্ছাকৃতভাবে interval বানাতে অস্বীকার করে।

### ৩.৭ যে তিনটি false-positive ফাঁদ বন্ধ করা হয়েছে

এগুলো না ধরলে corpus-এ ভুয়া provenance ঢুকে যেত:

1. **Curriculum label-কে entity ভাবা** — `subject_entities[0]` আসলে `_subtopic`-এর কপি (যেমন "প্রাচীন বাংলার জনপদসমূহ")। কোনো article-এ এই syllabus heading থাকে না, তাই match ratio কখনো 1.0 হতো না। বাদ দেওয়ার পর hit rate 50% → 90%।

2. **Aboutness** — শুধু token overlap দেখলে "বাংলার প্রথম স্বাধীন নবাব" fact-টি **নাটোর জেলা** article-এর সাথে 0.83 score পায়, কারণ সাধারণ ইতিহাস-শব্দ মিলে যায়। এখন article-কে entity **সম্পর্কে** হতে হয়, শুধু উল্লেখ করলে হবে না। Hit rate 90% → 67%, কিন্তু ভুয়া provenance-এর চেয়ে ফাঁক ভালো।

3. **বড় article ছোট article-কে হারিয়ে দেওয়া** — entity সংখ্যা দিয়ে ranking করলে "Lahore Resolution" fact-টি **Pakistan** article পেত, কারণ বড় article-এ বেশি entity থাকে। এখন title-specificity আগে।

### ৩.৮ Model B workflow (আলাদা ধারা)

| Item | সংখ্যা | অবস্থা |
|---|---:|---|
| Frozen Model B seed | 69 | **guard PASS ✅** |
| — static (pre-cutoff source প্রমাণ) | 63 | `temporal_evidence_date` 2021-07-07 – 2023-04-19 |
| — dynamic (`valid_from` প্রমাণ) | 6 | valid_from 1971 – 2009-01-06 |
| Pilot (লক্ষ্য) | 50 | চলমান |
| Batch 01 | 5 | সব deferred, 0 accepted |
| Batch 02 | 5 | 2 reviewed + 3 sourced |
| নতুন corpus-এ merge | **0** | হয়নি |

**আজকের Batch 02 কাজ:**

- **Review independence caveat** নথিভুক্ত। যাচাই করে পাওয়া গেছে RA_1 ও RA_2-এর `reason` string **হুবহু byte-identical** — দুটি স্বাধীন বিচার কখনো একই শব্দে লেখা হয় না। তাই `personnel_independent: false` লেখা হয়েছে।
- **Claim card** তৈরি — দুটি row আসলে **compound**: `BCSGK-0336`-এ ৫টি আলাদা দাবি এক row-তে, `BCSGK-0359`-এ ২টি।
- **৬টি candidate excerpt** automated ভাবে পাওয়া গেছে — **পড়ার পর একটিও গ্রহণযোগ্য নয়:**

| Claim | Verdict | কারণ |
|---|---|---|
| BCSGK-0336-D | **false positive** | `মহিষ` মিলেছে *"মহিষ মর্দিনী দেবীমূর্তি"*-তে — প্রাচীন দেবীমূর্তি, খামার নয় |
| BCSGK-0336-A | **false positive** | `প্রজনন` মিলেছে সাভারের পেশা-শতকরা টেবিলে — শ্রম পরিসংখ্যান, খামারের অবস্থান নয় |
| BCSGK-0359-A | partial | "বৃহত্তর ময়মনসিংহ" সমর্থিত, কিন্তু **সুনামগঞ্জ ও সিলেট নয়** (৭টির ৫টি) |
| BCSGK-0359-B | insufficient | `দুর্গাপুর` শুধু **image caption**-এ মিলেছে, কোনো বাক্যে নয় |
| BCSGK-0315-A | partial | পার্বত্য চট্টগ্রাম সমর্থিত, "চট্টগ্রাম জেলা" নয় |
| BCSGK-0336-E | weak | শিল্প-তালিকায় "কুমির খামার" আছে, কিন্তু নামধারী প্রতিষ্ঠান প্রমাণ হয় না |

> **শিক্ষা:** automated match একটা **lead**, evidence নয়। excerpt না পড়লে ২টি ভুয়া evidence corpus-এ ঢুকে যেত।

---

### ৩.৯ Step 1b — ১৩৫টি `unclassified` fact-এর triage

**সমস্যাটা কী ছিল:** triage topic দেখে শ্রেণি ঠিক করে — `Economy` → dynamic,
`History` → static। কিন্তু ১৩৫টি fact এমন ৮টি topic-এ ছিল যেগুলো কোনো তালিকাতেই
নেই: Bangladesh Affairs (53), Government (45), International Relations (8),
Education (7), Infrastructure (7), Sports (6), Science & Technology (5),
Defense (4)।

এই topic-গুলো **একরকম নয়** — তাই এদের জন্য blanket নিয়ম বানানো যায় না।
উদাহরণ: `Government`-এ একই সাথে আছে `সংবিধানের ৬৫(১) অনুচ্ছেদ` (সংশোধনী ছাড়া
বদলায় না) আর `বাংলাদেশের সিটি কর্পোরেশন ১২টি` (কয়েক বছর পরপর বদলায়)।

**সমাধান:** topic-এর বদলে **প্রতিটি fact-এর নিজের text** থেকে সিদ্ধান্ত।
যুক্তি relation inference-এর (Rule 14) মতোই — এটি দাবির *ব্যাকরণগত গঠন* পড়ে,
কোনো তারিখ আবিষ্কার করে না।

#### দুটি নকশা-নীতি (⚠️ এগুলো ভাঙবেন না)

**(ক) সন্দেহ হলে `dynamic` — কারণ ভুলের খরচ প্রতিসম নয়:**

| ভুল | পরিণতি |
|---|---|
| dynamic fact-কে `static` বলা | cutoff-পূর্ব source দিয়ে corpus-এ **ঢুকে যায়**, অথচ source আর দাবিটিকে সমর্থন করে না — **নীরব leak** |
| static fact-কে `dynamic` বলা | `valid_from` মেলে না, fact বাদ পড়ে — **recall কমে, কিন্তু কিছু দূষিত হয় না** |

তাই **সব dynamic rule সব static rule-এর আগে** চলে; দুটোই মিললে dynamic জেতে।

**(খ) কোনো rule অনুমান করে না।** কোনো rule না মিললে fact `unclassified` থেকে
যায় এবং reviewer queue-তে যায়। লক্ষ্য coverage নয়, **যাচাইযোগ্য basis**।

#### ফলাফল

| | সংখ্যা |
|---|---:|
| `static` নির্ধারিত | **64** |
| `dynamic` নির্ধারিত | **41** |
| এখনো অস্পষ্ট → reviewer queue | **30** |

প্রতিটি সিদ্ধান্তের `temporal_class_basis`-এ rule id, **যে শব্দটিতে মিলেছে**,
এবং কেন — তিনটিই লেখা থাকে। যেমন:

> `topic 'Defense' is on neither list; rule D4_mutable_superlative matched`
> `'flagship' in fact_text -- a superlative or rotating designation that`
> `another entity can take over`

#### অডিটে ধরা পড়া দুটি ভুল (শিক্ষণীয়)

১. **`BCSGK-0108`** — *"BNS Bangabandhu **is the flagship** of the Bangladesh
   Navy, commissioned in 2001"* → প্রথমে `static` হয়েছিল (তারিখযুক্ত ঘটনা)।
   কিন্তু দাবিটা commissioning নয়, **flagship থাকা** — যা হাতবদল হয়।
   `flagship` এখন dynamic cue।

২. **`BCSGK-0520`** — *"আইন ও সালিশ কেন্দ্র (আসক) ... ১৯৮৬ সালে প্রতিষ্ঠিত"* →
   `static` ঠিকই, কিন্তু rule ফায়ার করেছিল **ভুল কারণে**: বাক্যে `জনগোষ্ঠী`
   শব্দটি থাকায় নৃতাত্ত্বিক rule মিলেছিল। ফল ঠিক, **audit trail ভুল**।
   এখন দুই-সংকেতের S1 আগে চলে, তাই basis-এ আসল কারণ (১৯৮৬-র প্রতিষ্ঠা) লেখা হয়।

> এটি §৩.৭-এর সেই একই শিক্ষা: **সঠিক উত্তর আর সঠিক কারণ এক জিনিস নয়।**
> basis ভুল হলে পরের reviewer ভুল জিনিস যাচাই করবে।

#### Human adjudication ledger (নতুন)

যে সিদ্ধান্ত ব্যাকরণ দিতে পারে না — ৩০টি residual, আর topic-prior বনাম RA-পাঠের
দ্বন্দ্ব — তা `model_b_workflow/temporal_class_adjudications.jsonl`-এ যায়।
এগুলো **কখনো pattern rule-এ ঢোকানো হয় না**: এক ব্যক্তির এক বাক্যের পাঠ যেন
নীরবে আরও শত শত fact না নাড়ায়।

প্রয়োগের আগে চারটি শর্ত — একটিও না মিললে record **প্রয়োগ হয় না ও কারণ ছাপা হয়**:

1. `reviewer_status == "approved"`, সাথে `reviewer_id` ও `reason`
2. `previous_temporal_class` corpus-এর বর্তমান মানের সাথে মিলতে হবে (reviewer
   যা দেখেননি তার উপর replay হবে না)
3. fact frozen Model B seed-এ থাকলে **প্রত্যাখ্যাত** — seed বদলাতে re-freeze লাগে
4. `temporal_class` শুধু `static` বা `dynamic`

প্রয়োগ হওয়া প্রতিটি সিদ্ধান্ত append-only
`temporal_class_decision_ledger.jsonl`-এ যায় (rule-চালিত ১০৫টি সহ)।

```bash
python verify_temporal_corpus.py triage-unlisted           # শুধু প্রস্তাব
python verify_temporal_corpus.py triage-unlisted --apply   # corpus-এ লেখে
```

---

### ৩.১০ Guard layer — দুটি ফাঁক সংশোধন (⚠️ পড়া বাধ্যতামূলক)

Model B-র logic (`_has_verified_static_source_evidence()`) `kg_builder.py`-তে
**আগে থেকেই ছিল**। তবুও ৬৩টি static fact guard-এ আটকে যাচ্ছিল। কারণ দুটি:

#### ফাঁক ১ — Data plumbing: field-গুলো KG-তে পৌঁছাতোই না

`add_fact()` ও `insert_fact_pipeline()` `temporal_class` বা `temporal_evidence_*`
argument **নিত না**। ফলে `data.get("temporal_class")` সবসময় `None` → check সবসময়
`False` → ৬৩টি static fact চিরকাল `unversioned`।

> Logic ঠিক ছিল, কিন্তু তাকে খাওয়ানোর নল ছিল না।

**সমাধান:** পাঁচটি field plumb করা হয়েছে — `add_fact()` →
`insert_fact_pipeline()` → `main-pipeline.py`-র loader পর্যন্ত।

#### ফাঁক ২ — Silent Failure Bug: guard মিথ্যা `PASS` দিত

**এটি ছিল প্রকল্পের সবচেয়ে বিপজ্জনক bug।**

Model B চালু থাকলে retrieval function unversioned fact-গুলোকে `continue` দিয়ে
**বাদ** দিয়ে দিত। কিন্তু `strict_temporal_guard` ঠিক **ওই record খুঁজেই** violation
ধরে। ফলাফল:

> Guard **violation মিটিয়ে** নয়, **violation লুকিয়ে** `PASS` বলত।

৬১৬-fact corpus-এও এটি `PASS` দেখাত। ধরা না পড়লে পুরো holdout দাবিটাই অসার হয়ে যেত।

**মূল কারণ:** একটিই switch দিয়ে দুটি ভিন্ন প্রশ্নের উত্তর দেওয়া হচ্ছিল —

| প্রশ্ন | কী চায় | unversioned fact |
|---|---|---|
| **Retrieval** | "কোন fact থেকে MCQ বানাতে পারি?" | বাদ যাবে ✅ |
| **Audit** | "কোন fact এখনো অপ্রমাণিত?" | **দেখা যেতেই হবে** ✅ |

**সমাধান:** `drop_unversioned` নামে আলাদা parameter।
`strict_temporal_guard()` স্পষ্টভাবে `drop_unversioned=False` পাঠায়।

```python
# kg_builder.py — strict_temporal_guard()
facts = self.get_facts_by_topic_as_of(
    topic_name, as_of_date=as_of_date,
    allow_static_source_evidence=allow_static_source_evidence,
    drop_unversioned=False,   # guard-কে violation দেখতেই হবে
)
```

#### ⚠️ ভবিষ্যতের জন্য সতর্কতা

**`drop_unversioned` নিয়ে নাড়াচাড়া করার আগে এটি পড়ুন।** Audit ও retrieval logic
আবার গুলিয়ে ফেললে guard নীরবে মিথ্যা `PASS` দিতে শুরু করবে, এবং কোনো error দেখাবে না।

`test_cutoff.py`-র **TEST 7** এই bug-এর regression test — কেউ আবার এটি ঢোকালে
test fail করবে। **TEST 7 কখনো মুছবেন না।**

**যাচাই:** ৬১৬-fact corpus-এ guard **এখনো FAIL** করে (সঠিক), আর ৬৯-fact seed-এ
**PASS** করে (সঠিক)। দুটো একসাথে না মিললে ফলাফল বিশ্বাসযোগ্য নয়।

---

### ৩.১১ Evidence খোঁজার bug — markup-কে বাক্য ভাবা (⚠️ পড়া বাধ্যতামূলক)

**এই bug-টি একটি বাস্তব evidence লুকিয়ে রেখেছিল।**

Batch 02-এ `BCSGK-0359-B` (বিরিশিরি → দুর্গাপুর উপজেলা, নেত্রকোণা) "insufficient —
শুধু image caption-এ মিলেছে" বলে ফেরত পাঠানো হয়েছিল। handover-এ সুপারিশ ছিল
"infobox-এর district/upazila field থেকে re-extract করুন"।

**দুটোই ভুল ছিল।** article-এর infobox-এর সব field ফাঁকা — সেখান থেকে কিছু পাওয়া
সম্ভব ছিল না। আর evidence অনুপস্থিতও ছিল না — article-এর **প্রথম বাক্যটিই**
দাবিটা হুবহু বলে:

> '''বিরিশিরি''' [[নেত্রকোণা জেলা|নেত্রকোণা]] জেলার
> [[দুর্গাপুর উপজেলা|দুর্গাপুর উপজেলার]] ऐতিহ্যবাহী একটি গ্রাম।

**search সেখানে কখনো পৌঁছায়নি।** পুরনো `find_excerpt` যে window-এ দুটো term একসাথে
পেত তার **প্রথমটি** ফেরত দিত — আর infobox সবসময় আগে:

```
image_skyline = বিরিশিরির উপছবি.jpg ... image_caption = সুসং দুর্গাপুরের চিনামাটির পাহাড়
```

একটি **ছবির ফাইলের নাম** আর একটি **caption** — দুটো কাছাকাছি string, কোনো দাবি নয়।

#### খরচ দুদিকেই

| | |
|---|---|
| এখানে | আসল evidence **লুকিয়েছে**, fact বাদ পড়েছে |
| অন্য রো-তে | একই window **evidence হিসেবে গৃহীত হতে পারত** |

**সমাধান:** খোঁজার আগে non-prose অংশ ফাঁকা করা হয় — template, `<ref>`, ছবি-লিংক ও
তার caption, category ট্যাগ, এবং bare external link। প্রতিটি চরিত্র space দিয়ে বদলায়,
তাই offset অপরিবর্তিত থাকে। তারপর:

- **দুটো term-ই prose-এ থাকতে হবে** — prose subject + caption object সেই একই ফাঁদ, শুধু উল্টোদিকে
- সব candidate রাখা হয়, prose আগে; markup-only গুলো **rejected lead** হিসেবে লগ হয়
- excerpt বাক্যসীমায় (। / .) ছাঁটা হয়, যাতে reviewer অর্ধেক বাক্য পড়ে নিজে পূরণ না করেন

`test_excerpt_search.py`-এর **TEST 1** এই regression। **মুছবেন না।**

#### Batch 02-এর ফলাফল (`batch02_ra_sourcing.py followup`)

| Claim | ফল |
|---|---|
| `BCSGK-0359-B` বিরিশিরি | ✅ **সমর্থিত** — lead বাক্য, revision 2022-03-31 |
| `BCSGK-0359-A` সুনামগঞ্জ | ✅ **সমর্থিত** — *"এ জেলায় মনিপুরী, খাসিয়া, হাজং, গারো প্রভৃতি আদিবাসী জনগোষ্ঠীর বসবাস রয়েছে।"* (2023-03-29) |
| `BCSGK-0359-A` সিলেট | ❌ **সমর্থিত নয়** — সিলেট জেলা article-এ "গারো" শব্দটিই নেই |
| `BCSGK-0336` | ❌ **deferred** — §৩.১১ নিচে |

> ⚠️ **সিলেট নিয়ে একটি ফাঁদ এড়ানো হয়েছে।** সিলেট **বিভাগ** article-এ "গারো" আছে —
> *"পূর্ব কালে গারো, খাসীয়া ... আদিবাসীদের প্রাচীন কাব্য ... প্রভাব বাঙালি সংস্কৃতিতে পড়েছে"*।
> এটি (ক) জেলা নয়, বিভাগ; (খ) বর্তমান বসবাস নয়, অতীতের সাংস্কৃতিক প্রভাব।
> গ্রহণ করলে ভুয়া provenance ঢুকত। `do_not_substitute` হিসেবে লেখা আছে।

**ফলাফল:** `BCSGK-0359` এখন ৭টি জেলার মধ্যে ৬টি সমর্থিত। **রো-টি সিলেট
বলে বেশি দাবি করছে।** হয় claim সংকুচিত করতে হবে, নয়তো সিলেটের জন্য আলাদা source।

#### `BCSGK-0336` — কেন হলো না

dls.gov.bd-র Wayback capture আছে (২০০৭ থেকে), কিন্তু cutoff-পূর্ব portal
capture-গুলো সব প্রশাসনিক পাতা — monthly report, charter, notice, tender।
**চারটি সরকারি প্রজনন খামারের তালিকা কোথাও নেই।**

পঞ্চম atom (বেসরকারি কুমির খামার, ভালুকা) বেসরকারি — DLS তার জন্য ভুল কর্তৃপক্ষ
যতই খামার-তালিকা পাওয়া যাক।

> সৎতার শর্ত: Wayback CDX পুরো session-জুড়ে থেমে থেমে 503/429 দিয়েছে।
> এটি **একটি নির্দিষ্ট অনুসন্ধানের নেতিবাচঙ5 ফল**, অনুপস্থিতির প্রমাণ নয়।
> কী কী খোঁজা হয়েছে সব `batch02_followup_sourcing.jsonl`-এ লেখা।

---

### ৩.১২ Pilot batch — ৪৭টি fact-এর sourcing (`pilot_batch_sourcing.py`)

Batch 01/02 হাতে চলেছিল, পাঁচটি করে। বাকি ৪৭টির জন্য সেটা চলে না — তার দরকারও
নেই, টুকরোগুলো আগেই আছে: `resource_corpus` প্রতি fact-এর article আগেই ঠিক করে
রেখেছে ও revision cache করেছে, আর §৩.১১-এর সংশোধিত search এখন prose খোঁজে।

| ফল | সংখ্যা |
|---|---:|
| candidate evidence পাওয়া গেছে | **21** |
| কোনো prose সমর্থন মেলেনি | 25 |
| probe বানানো যায়নি | 1 |

#### এবং তারপর ৬ইটিই পড়া হয়েছে — এটাই আসল কাজ

| Verdict | সংখ্যা |
|---|---:|
| **full_support** | **1** |
| partial_support | 4 |
| insufficient | 3 |
| **false_positive** | **13** |

২১টির মধ্যে **মাত্র একটি** দাবিটি পূর্ণ সমর্থন করে (`BCSGK-0107`,
সশস্ত্র বাহিনী দিবস ২১ নভেম্বর)। Batch 02-এ ছিল ৬-এ শুন্য — একই চিত্র।

**দুটি শিক্ষণীয় ব্যর্থতা:**

- `BCSGK-0309` — excerpt: *"বাংলাদেশের সবচেয়ে বড় চা বাগান মৌলভীবাজারে"*।
  দাবি: মৌলভীবাজারে **সবচেয়ে বেশি** বাগান (৯০টি)। **একই জেলা, আলাদা দাবি।**
- `BCSGK-0074` — excerpt-এ আছে **বাতিল হয়ে যাওয়া** পরিকল্পনা (Ariane 5,
  ১৬ ডিসেম্বর ২০১৭), আর দাবি SpaceX, ১২ মে ২০১৮। গৃহীত হলে **ভুল তারিখ
  ও ভুল উৎক্ষেপক** fact-এ বসত।

> **নোট:** false positive মানে দাবিটি মিথ্যা নয় — মানে **এই excerpt সেটা
> সমর্থন করে না**। অনেকগুলো সম্ভবত সত্য ও অন্য দলিলে পাওয়া যাবে।

#### ⚠️ এই pass-এ নিজের একটি bug হয়েছিল — retract করা হয়েছে

প্রথম run-এ `probes_for()` entity-র `[নাম, TYPE]` জোড়া unwrap করেনি — probe হয়ে
গিয়েছিল আক্ষরিক `"['Shakib Al Hasan', 'PERSON']"`, যা কোনো article-এ নেই।
ফলে ৪৭টিই "no support found" লেখা হয়েছিল।

> **যে কারণে ধরা পড়ল:** ব্যর্থতা **১০০%** ছিল। আংশিক ব্যর্থতা হলে এটি
> corpus সম্পর্কে একটি বাস্তব আবিষ্কার বলে চালিয়ে যেত।

ledger append-only, তাই মোছা হয়নি — `pilot_batch_sourcing_retraction` record লেখা
হয়েছে, তারপর সংশোধিত run। ওই run শুধু log-এ লিখেছিল, evidence ledger-এ নয় —
**corpus-এ কোনো প্রভাব পড়েনি।**

#### `resourcing_proposals.json`-এর prose-support অডিট

`check_support()` পুরো wikitext-এ খোঁজে — infobox, category, ছবির নাম সবসহ।
মেপে দেখা গেছে: cache-এ থাকা **১৭২টি sourced proposal-এর মধ্যে ১১টি** prose-only
হিসেবে support হারায় (৬.৪%)।

**Gate বদলাইনি** — ওই ১৮১টি proposal PI-এর accept/reject-এর অপেক্ষায়, চুপচাপ
সরানো ঠিক হতো না। signal হিসেবে পাশে লেখা হয়েছে:
`proposal_prose_support_audit.jsonl`। ১০-১২ নং কাজে এটি দেখে নেবেন।

---

## ৪. যা যা বাকি — আপনার কাজের তালিকা

### ✅ গৃহীত সিদ্ধান্ত — Model B (Combined Model)

> এটি আর খোলা প্রশ্ন নয়। **Model B গৃহীত ও বাস্তবায়িত।**

Model B কোনো একক পদ্ধতি নয় — এটি একটি **Combined Model**, যা static ও dynamic
fact-কে তাদের নিজস্ব প্রকৃতি অনুযায়ী আলাদা নিয়মে প্রমাণ করে:

| শ্রেণি | প্রমাণের নিয়ম | seed-এ সংখ্যা |
|---|---|---:|
| **static** (কালাতীত) | যাচাইকৃত **cutoff-পূর্ব source** — `temporal_evidence_date ≤ t*` | **63** |
| **dynamic** (পরিবর্তনশীল) | যাচাইকৃত **`valid_from`** | **6** |
| | **মোট** | **69** |

**কেন static-কে `valid_from` দিয়ে মাপা যায় না:** BCS GK-র বেশিরভাগ fact কালাতীত —
"ঢাকা বাংলাদেশের রাজধানী", "সুন্দরবন দক্ষিণ-পশ্চিমে"। এদের `valid_from` চাওয়াটাই
ভুল প্রশ্ন। Geography-তে 87% sourced কিন্তু 0% dated — এটা bug নয়, fact-গুলোর প্রকৃতি।

**যুক্তি:** যদি একটি source প্রমাণিতভাবে cutoff-এর আগে ওই দাবিটি লিখে থাকে, তবে
cutoff-এ দাবিটি জানা ছিল — holdout-এর জন্য ঠিক এটাই দরকার। এটি label-এ বিশ্বাস নয়,
**evidence-এ ভিত্তি**।

**label-এ বিশ্বাস করা হয় না —** static route চালু হতে **পাঁচটি** field-ই লাগে:

```
temporal_class            == "static"
temporal_evidence_status  == "verified_pre_cutoff_source"
temporal_evidence_date    <= 2023-04-19
temporal_evidence_source_url      (থাকতে হবে)
temporal_evidence_snapshot_hash   (থাকতে হবে)
```

শুধু `temporal_class == "static"` দেখলে প্রতিটি ভুল শ্রেণিবিভাগ চুপচাপ leak হয়ে যেত —
ঠিক যে ব্যর্থতা ধরার জন্য holdout বানানো।

> **Design নোট:** MediaWiki revision date ইচ্ছাকৃতভাবে `source_published_at`-এ বসানো
> হয়নি, আলাদা `temporal_evidence_*` field-এ রাখা হয়েছে। কারণ revision timestamp প্রমাণ
> করে **evidence কখন ছিল**, দাবিটি **কখন প্রকাশিত** তা নয়। দুটো মিলিয়ে ফেললে audit
> trail নষ্ট হতো।

### 🔴 অগ্রাধিকার ১ — এখনো PI decision দরকার

> **পাঁচটি খোলা PI-সিদ্ধান্ত এক জায়গায়:**
> `model_b_workflow/pi_adjudication_queue.jsonl`
>
> এটি **index** — প্রতিটি item-এর পূর্ণ যুক্তি যে ফাইলে আছে সেখানেই দেখানো
> (`detail_record`)। সিদ্ধান্ত সেই মূল ফাইলে লেখা হয়, এখানে নয় — তাহলে
> দুটো কখনো আলাদা হয়ে যাবে না।
>
> | id | বিষয় | আটকাচ্ছে |
> |---|---|---|
> | `PI-BCE-DATES` | খ্রিষ্টপূর্ব তারিখ schema-তে লেখা যায় না | প্রাচীন ইতিহাসের fact |
> | `PI-COMPOUND-ROWS` | compound row ভাঙা যাবে কি (Rule 11) | `BCSGK-0336` পুরোটা |
> | `PI-0315-CLASS` | static না dynamic | `BCSGK-0315`-এর route |
> | `PI-Q263-COLLISION` | id সংশোধন করা যাবে কি | কিছু নয় (latent) |
> | `PI-0359-SCOPE` | সিলেট সমর্থিত নয় | `BCSGK-0359` |

**(২) BCE তারিখ** — "খ্রিষ্টপূর্ব ৩২১" (BCSGK-0142) বর্তমান `YYYY` schema-তে লেখা যায় না। আলাদা সিদ্ধান্ত দরকার।

**(৩) Compound fact ভাঙা হবে কি না** — `BCSGK-0336`-এ ৫টি স্বাধীন দাবি এক row-তে। এক excerpt দিয়ে প্রমাণ অসম্ভব। ভাঙতে হলে নতুন ID লাগবে, যা Rule 11-এর সাথে সাংঘর্ষিক।

### 🟠 অগ্রাধিকার ২ — আপনি এখনই করতে পারেন

**(৪) ১৩৫টি `unclassified` fact triage** — ✅ **সম্পন্ন (2026-09-17)**
৬৪টি `static`, ৪১টি `dynamic`, **৩০টি reviewer queue-তে**। §৩.৯ দেখুন।
বাকি ৩০টি পড়ার জন্য: `model_b_workflow/unlisted_topic_review_queue.csv`
(`reviewer_class` ও `reviewer_reason` কলাম দুটি ফাঁকা আছে)।

> ৩০টির মধ্যে যেগুলো সবচেয়ে সহজ — তারিখযুক্ত সম্পন্ন ঘটনা যা rule-এ ধরা পড়েনি
> কারণ ক্রিয়াপদটি তালিকায় নেই: `BCSGK-0087` (metro *began* operations 28 Dec 2022),
> `BCSGK-0090` (tunnel *completed* 2023), `BCSGK-0079` (jute genome *sequenced* 2010),
> `BCSGK-0084` (প্রথম টেস্ট ২০০০), `BCSGK-0372` (২১ জুলাই ১৯৯৪ প্রত্যাবর্তন)।
> এগুলো হাতে `static` লিখে ledger-এ দিলেই হয়। বাকিগুলো (যেমন `BCSGK-0550`
> "অ্যাটর্নি জেনারেল প্রধান আইন কর্মকর্তা") সত্যিই দ্ব্যর্থক।

**(৫) `BCSGK-0315`-এর শ্রেণি-দ্বন্দ্ব** — 🔴 **PI decision লাগবে** (অগ্রাধিকার ১-এ সরানো)

RA_1-এর নিজের record-ই (`batch02_ra_excerpt_adjudication.jsonl`) বলছে
*"PI adjudication is required"* — তাই এটি আপনি একা মেটাতে পারবেন না।
প্রশ্নটি পূর্ণ পটভূমিসহ খোলা আছে:
`model_b_workflow/temporal_class_adjudications.jsonl` (`reviewer_status: pending_pi`)।

উপস্থাপিত সুপারিশ — **static**, এই যুক্তিতে যে `topic=Economy` আসলে syllabus
label (`বিবিধ কৃষিজ ফসল`), দাবির temporality সম্পর্কে কোনো প্রমাণ নয় —
হুবহু §৩.৭-এর প্রথম ফাঁদটি (curriculum label-কে evidence ভাবা)।

> ⚠️ **এই সিদ্ধান্ত হলেও row-টি খুলছে না।** একটি স্বতন্ত্র blocker আছে:
> evidence শুধু পার্বত্য চট্টগ্রাম সমর্থন করে, **চট্টগ্রাম জেলা নয়**
> (`claim_scope_match: narrower_than_claim`)। অর্থাৎ static রায় দিলেও এই evidence-এ
> row-টি গ্রহণযোগ্য নয় — হয় চট্টগ্রাম জেলার source লাগবে, নয়তো claim সংকুচিত
> করতে হবে (যা আবার Rule 11-এর প্রশ্ন তোলে)।

**(৬) Batch 02** — 🟡 **দুটি সমাধান, একটি আটকে, একটি নেতিবাচক ফল** (§৩.১১)

| Claim | অবস্থা |
|---|---|
| `BCSGK-0359-B` বিরিশিরি | ✅ evidence পাওয়া গেছে (`EV_PILOT_000010`) — পড়া বাকি |
| `BCSGK-0359-A` সুনামগঞ্জ | ✅ evidence পাওয়া গেছে (`EV_PILOT_000011`) — পড়া বাকি |
| `BCSGK-0359-A` সিলেট | ❌ **সমর্থন নেই** — সিলেট জেলা article-এ "গারো" শব্দটিই নেই |
| `BCSGK-0359` রো | ⚖️ **partial_support** — PI queue-তে (`PI-0359-SCOPE`) |
| `BCSGK-0336` | ❌ deferred — DLS-এর খামার-তালিকার কোনো pre-cutoff capture নেই |
| `BCSGK-0315` | ⏸ (৫) মিটলে পর |

> **✅ সিদ্ধান্ত হয়েছে (2026-09-17):** `BCSGK-0359` **partial_support** হিসেবে
> চিহ্নিত, PI adjudication queue-তে রাখা হয়েছে (`PI-0359-SCOPE`)। সিলেট জেলার
> বিকল্প source (বাংলাপিড়িয়া / archive) পরে দেখা যাবে।
> **ততক্ষণ অসমর্থিত তথ্য যোগ করা যাবে না।**
>
> পুরনো work-status row-এ `evidence_status: complete` লেখা ছিল — সেটি এখন ভুল।
> ledger append-only, তাই একটি `work_status_correction` record লেখা হয়েছে যেটি
> ওটিকে supersede করে, মুছে নয়।
>
> একটি জিনিস PI-এর নজরে আনা দরকার: সমর্থিত ৬টির মধ্যে প্রথম পাঁচটি
> দাঁড়িয়ে আছে গারো article-এর **"বৃহত্তর ময়মনসিংহ"**-এর উপর, যা একটি
> ऐতিহাসিক গোষ্ঠীবিভাগ — বর্তমান জেলা-তালিকা নয়। শুধু সুনামগঞ্জ নিজের
> জেলা article থেকে সরাসরি সমর্থিত।

**(৭) External second reviewer** — 🟠 **packet পাঠানো হচ্ছে** (project lead, 2026-09-17)

তৃতীয় পক্ষের reviewer-এর কাছে পাঠানোর ব্যবস্থা চলছে। পাঠানোর নথি:

- `model_b_workflow/external_review_packet_BCSGK-0083_0109.md` — স্বয়ংসম্পূর্ণ;
  প্রকল্পের পূর্বজ্ঞান লাগে না। URL, hash, revision date, excerpt, এবং
  excerpt বনাম raw revision মিলানোর ধাপে ধাপে নির্দেশ আছে
- `model_b_workflow/external_review_response_template.json` — পূরণের ফরম

তিনটি URL যাচাই করা হয়েছে — তিনটিই HTTP 200।

> packet-এ দুটি প্রশ্ন স্পষ্ট করে তোলা হয়েছে যা আগের review এড়িয়ে গিয়েছিল:
> **(ক)** `BCSGK-0083`-এর evidence একজন মন্ত্রীর **উদ্ধৃত বক্তব্য** — তাতে কি
> "জাতীয় খেলা" পদমর্যাদা **প্রমাণিত** হয়, নাকি কেবল মন্ত্রী বলেছেন তাই?
> **(খ)** `BCSGK-0109`-এ "Bhatiary / Chittagong District" বনাম corpus-এর
> "Bhatiari, Chattogram" — নামের ভিন্নতা কি গ্রহণযোগ্য?

**(৮) Pilot-এর বাকি fact** — ✅ **৪৭টি source করা ও পড়া হয়েছে** (§৩.১২)

২১টি candidate evidence, প্রতিটি পড়া হয়েছে। **মাত্র ১টি পূর্ণ সমর্থন করে**
(`BCSGK-0107`), ৪টি আংশিক, ৩টি অপর্যাপ্ত, **১৩টি false positive**।
সব লেখা `model_b_workflow/pilot_batch_adjudication.jsonl`-এ।

> এই reading গুলো `auto_reading_v2` নামে, এবং
> `counts_toward_independent_review: false` — যে pass excerpt আনল সেই পড়লে
> সেটা স্বতন্ত্র review নয়। আপনার (বা RA-এর) পাঠ এখনও দরকার — তবে
> ২১টির মধ্যে ১৩টি ইতিমধ্যে কারণসহ বাতিল হওয়ায় আপনার কাজ অনেক কম।

বাকি: `BCSGK-0087`-কে সংশোধিত search দিয়ে re-source করতে হবে (পুরনো excerpt
একটি external link-এর label ছিল), আর ২৫টি fact-এর কোনো source মেলেনি —
সেগুলোর জন্য MediaWiki ছাড়া অন্য route লাগবে।

**(৯) `Q263` duplicate** — ✅ **তদন্ত শেষ, সিদ্ধান্ত PI-এর**
(`model_b_workflow/corpus_id_collision_adjudication.jsonl`)

উৎসে দুটি **আলাদা** প্রশ্ন একই `id: Q263` বহন করছে — ২৭তম বিসিএস (টেস্টটিউব
শিশু) ও ৩৫তম বিসিএস (সপ্তম পঞ্চবার্ষিক পরিকল্পনা)। এটি duplicate row নয়,
**id collision**।

যাচাই করা হয়েছে: কোথাও `_corpus_id` দিয়ে join করা হয় না — `resource_corpus.py`
ও `verify_temporal_corpus.py` দুটিই এটি শুধু label হিসেবে output row-এ কপি করে;
সব join `fact_uid`-এ, যা ৬১৬টির মধ্যে unique। **তাই এখনও কোনো ফলাফল দূষিত হয়নি।**

প্রস্তাব: ফাইল-ক্রমে **দ্বিতীয়টি** (৩৫তম বিসিএস) → `Q498`
(বর্তমান max `Q497`)। ID বদল = source-corpus edit, তাই PI sign-off লাগবে।

### 🟡 অগ্রাধিকার ৩ — decision-এর পরে

**(১০)** ১৮১টি resourcing proposal accept/reject করুন
**(১১)** Acceptance audit → `bcs_gk_facts_model_b_rc1.json` build (seed overwrite **নয়**)
**(১২)** Strict guard আবার চালান → pass হলে Task 1 COMPLETE

---

## ৫. ফাইল মানচিত্র

### Script

| ফাইল | কাজ |
|---|---|
| `verify_temporal_corpus.py` | Source যাচাই, triage, validation, audit, guard |
| `resource_corpus.py` | MediaWiki cutoff-পূর্ব revision থেকে re-sourcing |
| `batch02_ra_sourcing.py` | Batch 02 claim card, evidence, adjudication |
| `freeze_model_b.py` / `accept_model_b_proposals.py` | Model B seed ও acceptance |
| `test_triage_rules.py` | step-1b rule ও adjudication gate-এর regression test |
| `pilot_batch_sourcing.py` | **নতুন** — pilot-এর বাকি fact-এর evidence sourcing |
| `test_excerpt_search.py` | **নতুন** — markup/prose excerpt search-এর regression test |

### ডেটা

| ফাইল | কী আছে |
|---|---|
| `bcs_gk_facts.json` | মূল corpus, 616 fact (enriched) |
| `bcs_gk_facts_model_b.json` | **Frozen seed, 69 fact — হাত দেবেন না** |
| `temporal_metadata_audit.json` | পূর্ণ coverage audit |
| `temporal_metadata_review_report.json` | 616টি unresolved fact, কারণসহ |
| `temporal_verification_notes.json` | প্রতি fact-এর evidence record |
| `resourcing_proposals.json` | ৫৯৬টি প্রস্তাব (corpus-এ প্রয়োগ হয়নি) |
| `snapshots/wiki_revisions/` | Cached revision — পুনরায় fetch লাগবে না |
| `model_b_workflow/` | Pilot, ledger, review decision, adjudication |
| `model_b_workflow/unlisted_topic_review_queue.csv` | **নতুন** — ৩০টি অস্পষ্ট fact, পড়ার জন্য |
| `model_b_workflow/temporal_class_adjudications.jsonl` | **নতুন** — human class decision (append-only) |
| `model_b_workflow/temporal_class_decision_ledger.jsonl` | **নতুন** — যা যা প্রয়োগ হয়েছে (append-only) |
| `bcs_gk_facts.json.pre-triage.bak` | ১৩৫ triage-এর আগের corpus |
| `model_b_workflow/pilot_batch_adjudication.jsonl` | **নতুন** — ২১টি excerpt-এর পাঠ, verdict ও কারণ |
| `model_b_workflow/batch02_followup_sourcing.jsonl` | **নতুন** — Batch 02 follow-up, নেতিবাচক ফলসহ |
| `model_b_workflow/proposal_prose_support_audit.jsonl` | **নতুন** — ১৮১টি proposal-এর prose-support signal |
| `model_b_workflow/external_review_packet_*.md` | বাইরের reviewer-কে পাঠানোর নথি |
| `model_b_workflow/pi_adjudication_queue.jsonl` | **নতুন** — পাঁচটি খোলা PI-সিদ্ধান্তের index |

> দ্রষ্টব্য: রুট-এ রাখা `task1_handover.html` এই নথির একটি **পুরনো export**
> (2026-09-16 19:47)। সেটিতে §৩.৯–§৩.১২-এর কাজ নেই। এই `.md` ফাইলটিই source of truth।

### গুরুত্বপূর্ণ সতর্কতা

```
❌ bcs_gk_facts_model_b.json overwrite করবেন না
❌ evidence_ledger.jsonl-এর পুরনো line বদলাবেন না (append-only)
❌ excerpt না পড়ে accepted_for_build = true করবেন না
❌ source_url চুপচাপ বদলাবেন না — proposal হিসেবে রাখুন
❌ temporal_class_adjudications.jsonl-এর পুরনো line এডিট করবেন না — নতুন line যোগ করুন
❌ ৩০টি residual fact-এর জন্য rule চাপাতে যাবেন না — adjudication ledger ব্যবহার করুন
```

---

## ৬. যেসব test এখন pass করে

```bash
python -m py_compile verify_temporal_corpus.py resource_corpus.py main-pipeline.py \
       kg_builder.py test_cutoff.py test_triage_rules.py web_scraper.py   # PASS
python test_cutoff.py                                                 # PASS (20/20 CHECK)
python test_triage_rules.py                                           # PASS (28/28 CHECK)
python test_excerpt_search.py                                         # PASS (20/20 CHECK)
python test_task2_pipeline.py                                         # PASS (21/21 CHECK)
python verify_temporal_corpus.py guard --seed                         # PASS (69/69), exit 0
python test_dataset_accounting.py                                     # PASS
python test_pilot_evidence_intake.py                                  # PASS (4 test)
python verify_temporal_corpus.py validate                             # PASS (0 error, 1 warning)
python verify_temporal_corpus.py guard                                # FAIL (প্রত্যাশিত)
```

### `test_cutoff.py` — ১৯/১৯ CHECK True

| Test | বিষয় | ফল |
|---|---|---|
| TEST 1 | no cutoff | 1/1 ✅ |
| TEST 2 | cutoff = 2022-01-01 | 3/3 ✅ |
| TEST 3 | `facts_from_kg()` end-to-end | 1/1 ✅ |
| TEST 4 | strict benchmark gate | 3/3 ✅ |
| TEST 5 | Model B static-source evidence policy | 5/5 ✅ |
| **TEST 6** | **Model B static fact — নতুন** | 3/3 ✅ |
| **TEST 7** | **REGRESSION: guard hiding violations — নতুন** | 2/2 ✅ |

### `test_triage_rules.py` — ২৮/২৮ CHECK True (নতুন)

| Test | বিষয় | ফল |
|---|---|---|
| **TEST 1** | **এই rule দিয়ে আগেই শ্রেণিবদ্ধ কোনো fact বদলায় না** | 2/2 ✅ |
| **TEST 2** | **ফ্রোজেন Model B seed অক্ষত** | 2/2 ✅ |
| TEST 3 | দুটো cue মিললে dynamic জেতে | 2/2 ✅ |
| TEST 4 | S1-এ দুই সংকেত লাগে; বিচ্ছিন্ন সাল যথেষ্ট নয় | 3/3 ✅ |
| TEST 5 | কোনো rule অনুমান করে না | 3/3 ✅ |
| TEST 6 | প্রতিটি সিদ্ধান্তে rule id ও কারণ আছে | 1/1 ✅ |
| TEST 7 | হাতে পড়া ৬টি বাস্তব case | 6/6 ✅ |
| **TEST 8** | **Adjudication gate — pending / stale / seed / কারণহীন record প্রত্যাখ্যাত** | 9/9 ✅ |

> **TEST 1 ও TEST 2 কখনো মুছবেন না।** এই দুটিই প্রমাণ যে step-1b rule table
> পিছনে ফিরে গিয়ে আগের সিদ্ধান্ত — বিশেষত seed-এর ৬৯টি — নাড়াতে পারে না।

### `test_excerpt_search.py` — ২০/২০ CHECK True (নতুন)

| Test | বিষয় | ফল |
|---|---|---|
| **TEST 1** | **REGRESSION: infobox hit কখনো lead বাক্যের আগে যাবে না** | 5/5 ✅ |
| TEST 2 | markup-only hit evidence হিসেবে ফেরত যায় না | 2/2 ✅ |
| TEST 3 | দুটি term-ই masking-এ টিকতে হবে | 1/1 ✅ |
| TEST 4 | masking offset ঠিক রাখে; template/ref/category/link ফাঁকা হয় | 8/8 ✅ |
| TEST 5 | excerpt বাক্যসীমায় ছাঁটা হয় | 2/2 ✅ |

> **TEST 1 কখনো মুছবেন না।** এটি সেই bug-এর regression যেটি একটি বাস্তব
> evidence লুকিয়ে রেখেছিল (§৩.১১)।

### Model B seed-এ strict guard

```
static_valid_at_cutoff : 63
valid_at_cutoff (dyn)  :  6
usable মোট             : 69 / 69
guard violations       :  0

STRICT TEMPORAL GUARD (2023-04-19): PASS ✅
```

উৎস ৬১৬-fact corpus-এ `guard` fail করাটাই **সঠিক আচরণ** — ওই corpus holdout-এর জন্য
প্রস্তুত নয়, এবং tool সেটা লুকাচ্ছে না। **এই বৈপরীত্যটাই প্রমাণ** যে guard সত্যিই
যাচাই করছে, নিছক `PASS` বলছে না।

**Integrity যাচাই:** 616টি fact অক্ষত, কোনো field হারায়নি, কোনো `fact_text` / `source_url` / `_corpus_id` বদলায়নি, সব `fact_uid` unique, কোনো ভুল date format নেই।

---

## ৬ক. PI sign-off ও Task 2 প্রস্তুতি (2026-09-17)

### ৬ক.১ যে ফাঁকটি sign-off-এর আগে বন্ধ করতে হয়েছিল (⚠️)

Task 1-এর পুরো acceptance দাঁড়িয়ে আছে "69/69 strict guard PASS"-এর উপর।
কিন্তু সেই সংখ্যাটা এতদিন ছিল **কেবল নথিতে উদ্ধৃত একটি output block** — পুনরায়
চালানোর মতো কোনো command ছিল না।

কারণ: `run_guard()` `insert_fact_pipeline`-এ `temporal_class` বা চারটি
`temporal_evidence_*` field **পাঠাতই না**। ওগুলোই static route পড়ে। ফলে
offline dry-run Model B পরীক্ষা করতেই পারত না — সবসময় FAIL বলত।
§৩.১০-এর সেই একই plumbing ফাঁক, pipeline loader-এ সারানো হয়েছিল, এখানে বাদ পড়েছিল।

এখন সারানো, এবং acceptance একটি command-এ পুনরায় দেখানো যায়:

```bash
python verify_temporal_corpus.py guard --seed   # PASS, exit 0
python verify_temporal_corpus.py guard          # FAIL, exit 1 (প্রত্যাশিত)
```

```
static_valid_at_cutoff : 63
valid_at_cutoff (dyn)  :  6
usable total           : 69 / 69
guard violations       :  0
STRICT TEMPORAL GUARD (2023-04-19): PASS
```

> **দুটো একসাথে না মিললে ফলাফল বিশ্বাসযোগ্য নয়।** একই guard, একই cutoff,
> একই Model B route — ৬১৬-fact corpus-এ এখনো FAIL (৬০৩টি unversioned)।

### ৬ক.২ PI-এর পাঁচটি নির্দেশ — যা করা হয়েছে

| নির্দেশ | কার্যকর |
|---|---|
| Model B policy গৃহীত | `main-pipeline.py` আগে থেকেই wired; offline guard এখন মিলল |
| `BCSGK-0359` excluded | `active_build_exclusions.jsonl` |
| `BCSGK-0315` static, কিন্তু excluded | adjudication ledger দিয়ে প্রয়োগ, corpus-এ static |
| BCE তারিখ | ২টি fact-এ `temporal_evidence_notes`, numeric field null |
| Rule 11 কঠোর | `BCSGK-0336` excluded, কোনো row ভাঙা হয়নি |

**দুটি বিষয় PI-এর নজরে দরকার:**

১. **`PI-Q263-COLLISION` নির্দেশনায় ছিল না** — তাই resolved দেখানো হয়নি।
   কিছু আটকাচ্ছে না; দুটি fact-এর কোনোটিই seed-এ নেই।

২. **BCE নির্দেশ ও "seed intact" নির্দেশের মধ্যে একটি সংঘর্ষ** — `BCSGK-0142`
   seed-এর ভিতরে। তার numeric field আগে থেকেই null, অর্থাৎ মূল নিয়ম মানাই
   আছে। শুধু note-টি নেই। seed-এ note যোগ করলে sha256 বদলে যেত ও re-freeze
   লাগত — তাই **seed হাত দেইনি**, corpus-এ লিখে বিষয়টি জানালাম।

### ৬ক.৩ Task 2 — data path যাচাইকৃত, generation বাকি

`test_task2_pipeline.py` — **21/21 CHECK True**। যা প্রমাণিত:

- seed KG-তে পূর্ণ temporal metadataসহ ঢোকে (69 fact, 11 topic)
- প্রতিটি topic-এ strict guard PASS
- `facts_from_kg()` cutoff-এ **৫২টি** fact দেয় — কোনো unversioned নয়, কোনো
  post-cutoff তারিখ নয়
- ১১/১১ topic generation-এর যোগ্য; cap মেনে **২৫টি fact** লভ্য

> ৬৯ → ৫২ হলো quality gate-এ (`mcq_readiness < 0.5`), temporal কারণে নয়।

**যা চালানো যায়নি:** `.env` **০ বাইট** — কোনো HF key নেই। তাই Challenger /
 Reasoner / Judge তিনটি agent-ই চালানো হয়নি। **Task 2 সম্পন্ন নয়** —
এই একই seed-এ generation চালানো বাকি।

### ৬ক.৪ একটি ফাঁদ ভবিষ্যতের জন্য (⚠️)

`facts_from_kg()` `temporal_status` **লেবেল বসায়, filter করে না**। flag off করলেও
সেই একই fact ফেরত আসে, শুধু label বদলে `unversioned` হয়।

`main-pipeline.py` নিরাপদ **কেবল এই কারণে** যে সে generation-এর আগে
`strict_temporal_guard` চালায় ও RuntimeError তোলে। নতুন কোনো caller যদি
`facts_from_kg()` সরাসরি ডাকে guard ছাড়া — **৪৬টি unversioned fact সোজা
 generator-এ চলে যাবে**। `test_task2_pipeline.py` TEST 5 এটি নথিভুক্ত করে
রাখে। guard-ই gate, retrieval layer নয়।

---

## ৭. Task 1 কখন "COMPLETE" বলা যাবে

```
[x] Guard semantics সিদ্ধান্ত হয়েছে ও নথিভুক্ত — Model B গৃহীত
[x] Guard layer-এ Model B বাস্তবায়িত (field plumbing + drop_unversioned)
[x] 2023-04-19-এ strict temporal guard PASS — 69/69
[x] Regression test আছে (TEST 7) যাতে guard আর মিথ্যা PASS না দেয়
[x] কোনো তারিখ আবিষ্কার করা হয়নি — audit trail-এ প্রমাণিত

[x] ১৩৫টি unclassified fact triage — ১০৫টি rule-সহ নির্ধারিত, ৩০টি review queue-তে
[x] Class সিদ্ধান্তের audit trail (rule id + যে শব্দে মিলেছে + কেন) প্রতিটি fact-এ
[x] Human adjudication-এর পথ + চারটি gate + regression test
[x] `Q263` collision তদন্ত শেষ (join দূষণ হয়নি — যাচাইকৃত)
[x] Evidence excerpt search-এর markup bug সংশোধিত + regression test
[x] Pilot-এর ৪৭টি fact source করা, ২১টি excerpt পড়া ও verdict লেখা
[x] External review packet তৈরি (BCSGK-0083 / 0109)

[x] **PI sign-off — Task 1 COMPLETE (2026-09-17)**
[x] `guard --seed` দিয়ে 69/69 PASS পুনরায় যাচাইযোগ্য করা হয়েছে
[x] `BCSGK-0315` — PI রায়: static, কিন্তু build থেকে excluded
[x] BCE তারিখ — ২টি fact-এ raw text note, numeric null
[x] Rule 11 — কোনো compound row ভাঙা হয়নি; `BCSGK-0336` excluded
[x] Task 2 data path যাচাই (21/21)

[ ] Task 2 generation — HF key লাগবে (`.env` বর্তমানে খালি)
[ ] `PI-Q263-COLLISION` — নির্দেশনায় ছিল না, খোলা
[ ] ৩০টি residual fact-এর reviewer decision
[x] `BCSGK-0359` — partial_support রায়, PI queue-তে তোলা (`PI-0359-SCOPE`)
[x] পাঁচটি খোলা PI-সিদ্ধান্ত এক index-এ (`pi_adjudication_queue.jsonl`)

[ ] `BCSGK-0359` — সিলেটের বিকল্প source (বাংলাপিড়িয়া / archive) — পরে
[ ] External reviewer-এর ফিরতি পাওয়া (packet পাঠানো হচ্ছে)
[ ] PI মিটিং — পাঁচটি item-এর রায়
[ ] ২১টি pilot excerpt-এর মানুষের পাঠ (auto_reading গণ্য নয়)
[ ] Pilot-এর ৫০টি fact-এর evidence outcome logged
[ ] BCSGK-0083 / 0109-এ external second review হয়েছে
[ ] Resourcing proposal accept / reject সম্পন্ন
[ ] rc1 build হয়েছে (seed overwrite ছাড়া)
```

**মূল acceptance criterion (strict guard PASS) পূরণ হয়েছে ✅** — তাই Task 2
প্রযুক্তিগতভাবে আর আটকে নেই।

তবে সৎভাবে বললে: **release corpus এখন মাত্র ৬৯টি fact**। বাকি ঘরগুলো guard
আটকাচ্ছে না, কিন্তু corpus-এর *আকার* ও *review-এর মান* বাড়াতে ওগুলো দরকার।
PI-এর সিদ্ধান্ত — ৬৯ নিয়ে Task 2 শুরু করবেন, নাকি pilot শেষ করে corpus বড় করে নেবেন।

---

## ৮. এক লাইনে সারকথা

> Corpus-এর citation layer ভুয়া ছিল; সেটা ধরা পড়েছে, মাপা হয়েছে, এবং প্রমাণভিত্তিক
> বিকল্প (Model B) তৈরি, গৃহীত ও **বাস্তবায়িত** হয়েছে। BCS GK-র বেশিরভাগ fact
> কালাতীত — তাই static-কে প্রমাণ করা হয় cutoff-পূর্ব source দিয়ে, dynamic-কে
> `valid_from` দিয়ে। **69/69 fact এখন strict guard pass করে।**

একটা কথা মনে রাখবেন: **৬৯টি fact নিয়ে strict guard pass করা corpus প্রকাশযোগ্য;
৬১৬টি fact নিয়ে বানানো তারিখের corpus নয়।**

---

## ৯. Task 2B — Quality Gate Upgrade (পরবর্তী session-এর কাজ)

> **এই section-টি নতুন session-এর প্রবেশপথ।** Task 1 COMPLETE, Task 2A সম্পন্ন।
> Task 2B শুরু করার আগে নিচের "শুরু করার আগে" অংশটি চালান।

### ৯.১ কেন Task 2B দরকার — Task 2A-র বাস্তব output

Smoke run ৭টি MCQ বানিয়েছে (Geography ৩, Culture ৪)। Culture ব্যাচে quality gate
**৪/৪ pass** দিয়েছে। তবু output ব্যবহারযোগ্য নয়:

| সমস্যা | উদাহরণ | gate কী বলেছে |
|---|---|---|
| বানান | `পৌহেলা` (→ পহেলা), `বুরিগঙ্গা` (→ বুড়িগঙ্গা), `পাদমা` (→ পদ্মা) | ধরেনি |
| অনুবাদ | `secular` → **`বিশ্বাস্ত`** (অর্থ "বিশ্বস্ত"); হওয়া উচিত `ধর্মনিরপেক্ষ` | ধরেনি |
| সমার্থক distractor | সঠিক উত্তর *পহেলা বৈশাখ*, distractor **`নববর্ষ`** — একই জিনিস | distractor 0.75, **passed** |
| প্রায়-নকল প্রশ্ন | Geography-র ৩টির ২টিই "ঢাকা কোন নদীর তীরে" | ধরেনি |

> **সতর্কতা:** একটি দাবি প্রথমে ungrounded মনে হয়েছিল ("বৃহত্তম উৎসব")। আসলে
> source fact-এ *"the largest secular festival"* আছে — সমস্যা grounding-এ নয়,
> **অনুবাদে**। পরের pass-এ এই পার্থক্যটা ধরে রাখবেন: ভুল অনুবাদ আর ভিত্তিহীন
> দাবি এক জিনিস নয়, আর দুটোর প্রতিকারও আলাদা।

### ৯.২ তিনটি কাজ (PI-নির্দেশিত)

**(ক) Prompt-level constraint** — `ChallengerAgent`-এর প্রম্পটে কঠোর নির্দেশ:
প্রমিত বাংলা বানান, এবং সঠিক উত্তরের সমার্থক শব্দ distractor হিসেবে নিষিদ্ধ।

> প্রম্পট বদলালে `task2a_smoke_run.py`-র `prompt_fingerprint` আপনাআপনি বদলাবে
> (source hash), তাই আগের ও পরের run আলাদা করা যাবে।

**(খ) Orthography + distractor validator** — `RuleBasedScreener`-এ যোগ করুন।
এটি LLM-মুক্ত স্তর, তাই সস্তা ও deterministic:

- সাধারণ ভুল বানানের controlled তালিকা (পৌহেলা→পহেলা, বুরিগঙ্গা→বুড়িগঙ্গা, পাদমা→পদ্মা)
- option-গুলোর মধ্যে exact/normalized match এবং পরিচিত সমার্থক জোড়া
  (পহেলা বৈশাখ ≡ বাংলা নববর্ষ ≡ নববর্ষ)

**(গ) Translation guard** — ইংরেজি fact থেকে বাংলা MCQ বানানোর পর মূল শব্দগুলো
ঠিকভাবে অনুবাদ হয়েছে কিনা (secular → ধর্মনিরপেক্ষ ইত্যাদি)।

### ৯.৩ শুরু করার আগে (ক্রম মেনে)

```bash
# ১. নতুন least-privilege token বসান — .env-এ দুটো নামেই একই মান
#    HF_API_KEY=...   HF_TOKEN=...
python -c "from dotenv import load_dotenv; load_dotenv(); import os; \
  assert os.getenv('HF_API_KEY'), 'HF_API_KEY missing'; print('key present')"

# ২. state অপরিবর্তিত কিনা
python verify_temporal_corpus.py guard --seed      # PASS, exit 0
python test_task2_pipeline.py                      # 21/21
python test_generation_firewall.py                 # 10/10

# ৩. তারপরই কেবল Task 2B-র কোড বদলান
```

> `.env`-এ token **খালি রাখা হয়েছে ইচ্ছাকৃতভাবে**। পুরোনোটি revoke করা;
> revoke-করা token রেখে দিলে পরে এমন 401 আসত যা model/provider-এর সমস্যা বলে মনে হয়।

### ৯.৪ যা ছোঁবেন না

```
❌ frozen seed (bcs_gk_facts_model_b.json) — sha256 manifest-এ pinned
❌ BCSGK-0359 / 0315 / 0336 — PI-নির্দেশে build path-এর বাইরে
❌ Q263 — এখনো open, resolved দেখাবেন না
❌ generate_from_facts()-এর temporal firewall — শিথিল করবেন না
❌ task2a_smoke_run.jsonl-এর record — smoke output, benchmark data নয়
```

### ৯.৫ Task 2B কখন সম্পন্ন

```
[x] প্রম্পটে বানান ও distractor constraint যোগ, fingerprint বদলেছে
[x] RuleBasedScreener সমার্থক distractor ধরে (পহেলা বৈশাখ বনাম নববর্ষ → FAIL)
[x] Orthography checker চিহ্নিত ৩টি ভুল বানান ধরে
[x] Translation guard secular → ধর্মনিরপেক্ষ যাচাই করে
[x] Task 2A-র ৭টি পুরনো MCQ নতুন gate-এ চালালে সমস্যাগুলো FAIL করে
    (regression fixture হিসেবে ব্যবহার করুন — এগুলোর ত্রুটি জানা)
[x] নতুন bounded batch চালিয়ে পাস/ফেল যুক্তিসঙ্গত
```

> **সবচেয়ে সস্তা disproof test:** নতুন checker লেখার পর আগে
> `task2a_smoke_run.jsonl`-এর ৭টি রেকর্ডে চালান। যেগুলোর ত্রুটি উপরে তালিকাভুক্ত
> সেগুলো FAIL না করলে checker কাজ করছে না — নতুন generation চালানোর দরকার নেই।
