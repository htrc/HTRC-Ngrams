# HTRC N-grams Extraction Pipeline

A highly optimized, non-consumptive N-gram extraction engine natively engineered for massive HathiTrust Research Center Pairtree volumes on high-performance computing clusters (e.g. BigRed200). 

---

## 🚀 Quick Start

### Installation
Load the exact Python environment and install the required machine learning dependencies onto your cluster node:
```bash
module load python
pip install -r requirements.txt
```

### Execution
The entrypoint for the application is the central orchestrator `run_pipeline.py`. Execute the script by passing the physical path to the Pairtree store and your target HTID list.

#### Required Arguments
* `--pairtree`: Absolute path to the physical HathiTrust Pairtree root.
* `--id-list`: Absolute path to a plaintext mapping file of Volume HTIDs.

#### Filter Tuning (Optional)
* `--min-freq`: Minimum volume-level frequency threshold for Filter 2 (default: `2`).
* `--min-pmi`: Minimum PMI threshold for Filter 3 (default: `0.0`).
* `--max-exposure`: Maximum token exposure ratio limit for Filter 4 (default: `0.15`).

#### Example Command
```bash
python run_pipeline.py \
  --pairtree /N/project/htrc/data/ht_text \
  --id-list htids.txt \
  --min-freq 2 \
  --min-pmi 0.0 \
  --max-exposure 0.15
```

## 🖥️ HPC Deployment (BigRed200)

Because this pipeline is specifically engineered for Indiana University's BigRed200 Supercomputer, local execution is constrained. To run the pipeline natively against the massive million-volume corpus on BigRed200, you must utilize the SLURM workload manager.

### 1. Beam the Code to the Cluster
Use `scp` to securely copy the orchestrator, SLURM scheduler scripts, and the core structural `src/` package directly onto your cluster node allocation:
```bash
scp run_pipeline.py umkatta@bigred200.uits.iu.edu:/N/project/htrc/HTRC-Ngrams/
scp submit_bigred.slurm submit.sh umkatta@bigred200.uits.iu.edu:/N/project/htrc/HTRC-Ngrams/
scp -r src umkatta@bigred200.uits.iu.edu:/N/project/htrc/HTRC-Ngrams/
```

### 2. Physical Data Paths
The extraction engine physically links to the dedicated HathiTrust storage mount point natively wired into BigRed200. Ensure your `submit_bigred.slurm` script correctly points to this static global Pairtree root:
* **Storage Root:** `/N/project/htrc/data/ht_text`
* **ID Mapping:** Make sure your `htids.txt` (the list of target volume IDs) is securely located in the exact same directory as your SLURM execution script. 

### 3. Launch the Job
Once your files successfully upload, securely SSH into BigRed200, navigate to the `HTRC-Ngrams/` launch directory, and queue the node allocation using the dynamic array wrapper:
```bash
ssh umkatta@bigred200.uits.iu.edu
cd /N/project/htrc/HTRC-Ngrams/
bash submit.sh
```

### 4. Output Files
The pipeline automatically manages cluster-safe locking to produce unified outputs:
* `exported_bigrams/` - The permanent Pairtree directory containing the finished `.tsv` files.
* `bigred_results.csv` - A single, atomic summary CSV containing statistics for every processed volume.
* `pending_volumes.txt` - A real-time tracker of any volumes that failed to process (e.g., oversized, no tokens, missing zip).
* `logs/` - Contains the individual SLURM `.out` logs for the active array tasks.

## 📊 Package Structure
* `run_pipeline.py` - Core execution orchestrator.
* `submit.sh` - Dynamic SLURM array wrapper script.
* `submit_bigred.slurm` - SLURM HPC job submission script.
* `requirements.txt` - Python module dependencies.
* `src/` - Encapsulated mathematical and NLP engines.

---

## 🧠 Pipeline Architecture (The 5 Filters)

This pipeline processes massive historical books into useful datasets ("N-grams") without violating copyright or data-sharing laws. Our core directive is **Non-Consumptive Research**. This means we mathematically guarantee that it is impossible for anyone to read or reconstruct the original sentences of the book from our exported data using our 5 security steps (Filters 0 through 4).

### Filter 0: Text Healing and Language Tagging
Before computational math is applied, the text is formally stabilized: 
1. **The "Page-Wall" Safety Guard:** Every single page is processed as an isolated island, completely dropping boundary shards to guarantee cross-page bigrams cannot physically merge.
2. **End-of-Line Hyphenation Healing:** Scanners often break single words across two lines (`govern- \n ment -> government`). The software automatically restores them. 
3. **Automatic Language Detection:** The ML engine bypasses massive English title pages by dropping sequentially into Page 20 and sampling 10 narrative pages to dynamically tag the entire volume's language profile.
4. **Structural Cleanup:** Knowing the correct language allows the software to strip out specific garbage (like the French header `CHAPITRE`) and safely drop 1-letter fragments (like an OCR shattered `t` or `r`) while protecting valid international letters (like `à` or `y`).

### Filter 1: The Top-25 Stopword Removal
Instead of using a hard-coded list of English stopwords, the software calculates the 25 most frequent tokens **dynamically for every single book** based purely on physical occurrence arrays. It then deletes any phrase if *both* words are in that Top 25 list (for example, `"of the"`).

### Filter 2: The Core Security Wall (Volume Minimums)
This is the primary defense against text-reconstruction.
If a bigram (e.g., a two-word phrase) appears exactly 1 or 2 times in a massive 400-page book, it is highly likely to be a highly-unique sentence fragment. If exported, a smart system could piece the fragments together to reconstruct the author's original sentence.
*   **The Rule:** If a phrase does not appear at least `X` times in the book (our pilot defaults to `X=2`), it is permanently deleted.

### Filter 3: Pointwise Mutual Information (PMI) Polish
Filter 1 and 2 remove the garbage and dangerous data. Filter 3 mathematically isolates the "Quality" of the data that survived. To determine if two words actually belong together conceptually, we use **PMI (Pointwise Mutual Information)**. 
*   **The Calculation:** `PMI = probability of the phrase / (probability of word 1 * probability of word 2)`
*   **The Math:** We calculate how often the two words exist side-by-side, divided by how often they theoretically *should* bump into each other completely randomly.

If the score is positive, it means the words are tightly bonded and hold deep semantic meaning. If the score is negative (like `-1.5`), it means the words mathematically repel each other, and only ended up next to each other because of an OCR column-tear. Filter 3 lightly trims these negative repulsions based on the `--min-pmi` threshold.

### Filter 4: The Maximum Exposure Cap
Filter 4 is the absolute ultimate safeguard. Even if a book passes Filters 1, 2, and 3, it cannot be legally distributed if the resulting pure phrases naturally represent over 60% of the entire book. Filter 4 mandates that the final export dataset can **never represent more than a designated cap (default 15%) of the total word-mass of the book.**
*   **The Budget:** The engine calculates exactly how many words are inside the book. A 15% cap on a 100,000 word mass legally allows a payload distribution of 15,000 words. 
*   **The Ranking:** It primarily sorts the surviving dataset by Frequency, using the PMI score as a secondary ranking Tie-Breaker.
*   **The Trimming:** The software begins dumping the absolute highest ranked arrays into the final output pool. The split-second the accumulated word-mass breaches the allowed budget, the software physically breaks the computational sequence and trims all surviving data!
