# ☁️ Streamlit Community Cloud Deployment Guide: BCSBatighor-GK Showcase

This guide provides step-by-step instructions to deploy the **BCSBatighor-GK Interactive Dashboard** to **Streamlit Community Cloud** (`share.streamlit.io`) for free public hosting, accessible on your MacBook during your presentation to **Dr. Sumaiya Tabassum Nimi**.

---

## 💻 Step 1: Run & Test Locally on your PC

Before pushing to GitHub, you can launch and test the dashboard on your local machine:

```bash
streamlit run app.py
```
This will open the dashboard in your default browser at `http://localhost:8501`.

---

## 🐙 Step 2: Push your Project to GitHub

1. Initialize git and commit your files (if not already initialized):
   ```bash
   git add app.py requirements.txt DEPLOYMENT_STREAMLIT_CLOUD.md
   git commit -m "Add Streamlit Showcase Dashboard for BCSBatighor-GK"
   ```

2. Create a new repository on [GitHub](https://github.com/new):
   - **Repository name**: `BCSBatighor-GK-Showcase`
   - **Visibility**: Public (recommended for standard free Streamlit Cloud hosting)

3. Connect your local repository and push:
   ```bash
   git remote add origin https://github.com/YOUR_GITHUB_USERNAME/BCSBatighor-GK-Showcase.git
   git branch -M main
   git push -u origin main
   ```

---

## 🚀 Step 3: Deploy on Streamlit Community Cloud

1. Go to **[share.streamlit.io](https://share.streamlit.io/)** and log in with your GitHub account.
2. Click **"New app"** (or **"Create app"**).
3. Select your repository details:
   - **Repository**: `YOUR_GITHUB_USERNAME/BCSBatighor-GK-Showcase`
   - **Branch**: `main`
   - **Main file path**: `app.py`
4. Click **"Deploy!"**.

Streamlit Community Cloud will automatically install dependencies from `requirements.txt` and launch your dashboard in ~1 minute!

---

## 📱 Accessing the Live Link on your MacBook

Once deployed, Streamlit will give you a permanent live public URL, for example:
`https://bcsbatighor-gk-showcase.streamlit.app`

You can open this URL directly in Safari or Chrome on your **MacBook**, **iPad**, or **Phone** during your presentation to Dr. Sumaiya Tabassum Nimi!

---

## 📑 Application Features Included
- **Sidebar**: Project Info, Supervisor (*Dr. Sumaiya Tabassum Nimi*), Cutoff Date ($t^* = \text{2023-04-19}$), Compliance Score ($100.0\%$).
- **Tab 1: Live MCQ Generator & Cutoff Sandbox**: Interactive domain selection, generator variant comparison (*Proposed BKG* vs *Web-RAG* vs *Static RAG* vs *Generic LLM*), real-time Quality Gate badge, and evidence provenance drawer.
- **Tab 2: Bitemporal KG Visualizer**: Time-Travel slider (1970–2026) demonstrating valid vs superseded nodes with Plotly graph and snapshot ledger.
- **Tab 3: Scientific Metrics & Hypothesis Testing**: Interactive KPI cards, Plotly comparison matrices, $H_1 - H_5$ statistical hypothesis table, and ablation study breakdown.
- **Tab 4: 45th BCS Real Exam Holdout**: Real exam matching analysis (MRR = 0.6818, Recall@1 = 0.6818) with side-by-side question inspector.
