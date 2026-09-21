# 🚀 Hugging Face Spaces Deployment Guide: BCSBatighor-GK Web Showcase

This guide provides step-by-step instructions to deploy the **BCSBatighor-GK Web Application** to **Hugging Face Spaces** for permanent, free, public cloud hosting.

---

## 📋 Option A: Instant Local Sharing (MacBook Access without Cloud Upload)

If you just want to run the app on your home PC and view it on your MacBook during a meeting with Dr. Sumaiya Tabassum Nimi:

1. Open Terminal on your home PC in the project folder (`d:\BCS_final`):
   ```bash
   python run_demo.py
   ```
2. Look for the generated public link in terminal output:
   ```text
   Running on public URL: https://xxxxxxxxxxxxxxxx.gradio.live
   ```
3. Copy that `https://xxxx.gradio.live` link and open it in Safari/Chrome on your MacBook.

---

## ☁️ Option B: Free Permanent Cloud Hosting on Hugging Face Spaces

To deploy the app permanently on Hugging Face Spaces so it runs 24/7 without keeping your home PC turned on:

### Step 1: Create a New Space on Hugging Face
1. Log in to [Hugging Face](https://huggingface.co/).
2. Click on your profile icon at top right -> **New Space**.
3. Fill in the details:
   - **Space Name**: `BCSBatighor-GK-Demo`
   - **License**: `mit`
   - **Select the Space SDK**: Choose **Gradio**
   - **Space Hardware**: Choose **CPU Basic (Free)**
   - **Visibility**: `Public`
4. Click **Create Space**.

### Step 2: Upload Files to the Space
You can push files using Git or upload them directly in the browser on Hugging Face:

#### Required Files to Upload:
1. `app.py` (Main web application)
2. `requirements.txt` (Dependencies list)
3. `kg_builder.py`
4. `rejection_taxonomy.py`
5. `bcs_gk_facts.json`
6. `bcs_questions_corpus.json`

#### Using Git CLI:
```bash
git clone https://huggingface.co/spaces/YOUR_USERNAME/BCSBatighor-GK-Demo
cd BCSBatighor-GK-Demo

# Copy app files into the cloned folder
cp d:\BCS_final\app.py .
cp d:\BCS_final\requirements.txt .
cp d:\BCS_final\kg_builder.py .
cp d:\BCS_final\rejection_taxonomy.py .
cp d:\BCS_final\bcs_gk_facts.json .
cp d:\BCS_final\bcs_questions_corpus.json .

git add .
git commit -m "Deploy BCSBatighor-GK showcase app"
git push
```

### Step 3: View Live Space
Once pushed, Hugging Face will automatically build and start the Gradio app!
Your permanent public URL will be:
`https://huggingface.co/spaces/YOUR_USERNAME/BCSBatighor-GK-Demo`

---

## 📱 Features Available in the Web Showcase
- **Tab 1: Live MCQ Generator & Cutoff Sandbox** (`t* = 2023-04-19`) with real-time Quality Gate status badge and taxonomy diagnostics (`E-TIME`, `E-LEAK`, `E-UNSUP`).
- **Tab 2: Bitemporal KG Visualizer** with interactive time-travel year slider (1970–2026) demonstrating valid vs superseded nodes.
- **Tab 3: Comparative Scientific Benchmark** with Plotly metrics, Hypothesis testing results (H1-H5), and Component Ablation drops.
- **Tab 4: 45th BCS Holdout Real Exam Verification** (MRR = 0.6818, Recall@1 = 0.6818).
