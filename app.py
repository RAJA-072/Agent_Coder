import os
import re
import json
import shutil
import tempfile
import requests
import subprocess
from flask import Flask, request, jsonify, render_template
import google.generativeai as genai
from repo_handler import process_repository

app = Flask(__name__, static_url_path="/static", static_folder="static", template_folder="templates")

# ========== CONFIG ==========
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
genai.configure(api_key=GEMINI_API_KEY)
MODEL_NAME = os.getenv("GEMINI_MODEL", "gemini-2.0-flash")
MAX_CONTEXT_CHARS = int(os.getenv("MAX_CONTEXT_CHARS", "16000"))
# ===========================

repo_cache = {}  # in-memory cache


# ---------- Helpers ----------
def fetch_github_metadata(repo_url: str) -> dict:
    """Fetch repo metadata from GitHub REST API."""
    owner_repo = "/".join(repo_url.rstrip("/").split("/")[-2:])
    url = f"https://api.github.com/repos/{owner_repo}"
    r = requests.get(url, timeout=20)
    if r.status_code != 200:
        raise RuntimeError(f"GitHub API error {r.status_code}: {r.text[:200]}")
    data = r.json()
    return {
        "Name": data.get("name"),
        "Full Name": data.get("full_name"),
        "Description": data.get("description") or "No description provided.",
        "Stars": data.get("stargazers_count"),
        "Forks": data.get("forks_count"),
        "Open Issues": data.get("open_issues_count"),
        "Language": data.get("language"),
        "License": (data.get("license") or {}).get("name", "None"),
        "URL": data.get("html_url"),
    }


def clone_and_collect(repo_url: str):
    """Clone repo into temp dir and return a digest + file list."""
    tmpdir = tempfile.mkdtemp()
    try:
        subprocess.run(["git", "clone", "--depth", "1", repo_url, tmpdir], check=True, capture_output=True)
        collected = []
        file_list = []

        for root, _, files in os.walk(tmpdir):
            for fname in files:
                fpath = os.path.join(root, fname)
                relpath = os.path.relpath(fpath, tmpdir)

                if os.path.getsize(fpath) > 200_000:  # skip large/binary
                    continue
                try:
                    with open(fpath, "r", encoding="utf-8", errors="ignore") as f:
                        content = f.read()
                    collected.append(f"# File: {relpath}\n{content}\n")
                    file_list.append(relpath)
                except Exception:
                    continue

        digest = "\n".join(collected)
        return digest, file_list
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def sanitize_answer(s: str) -> str:
    """Clean answer text for UI."""
    if not isinstance(s, str):
        s = str(s)
    s = re.sub(r"```.*?```", "", s, flags=re.S)
    s = re.sub(r"^[#*\-\s]+", "", s, flags=re.M)
    s = re.sub(r"\*{1,3}", "", s)
    s = re.sub(r"`+", "", s)
    return s.strip()


def build_system_prompt(metadata: dict, files: list, digest_snippet: str, persona: str):
    """Build system prompt for Gemini model."""
    meta_pretty = json.dumps(metadata, indent=2, ensure_ascii=False)
    files_block = "\n".join(f"- {p}" for p in files[:20]) or "(no files found)"
    # Few-shot examples for each persona
    examples = {
        "student (beginner)": "Q: What does this repo do?\nA: This project is like a recipe book for computers. It helps you do X by following simple steps.\n\nQ: What is a function here?\nA: A function is like a mini-machine that does a specific job, such as adding numbers.",
        "student (intermediate)": "Q: How does the main module work?\nA: The main module loads data, processes it, and outputs results. It uses functions like load_data() and process().\n\nQ: What is the role of requirements.txt?\nA: It lists the Python packages needed to run the project.",
        "student (advanced)": "Q: How is error handling implemented?\nA: The code uses try/except blocks to catch exceptions, especially in data loading and API calls.\n\nQ: How would you refactor the main loop?\nA: Consider extracting logic into smaller functions and using list comprehensions for clarity."
    }
    persona_instructions = {
        "student (beginner)": "Explain concepts simply, use analogies, avoid jargon, and break down complex ideas. If the question is unclear, ask a clarifying question first.",
        "student (intermediate)": "Give clear, step-by-step explanations. Use code snippets and bullet points. If the question is vague, ask for clarification.",
        "student (advanced)": "Provide in-depth, technical answers. Use code, discuss trade-offs, and suggest improvements. If the question is ambiguous, request more detail."
    }
    persona = persona.lower().strip()
    persona_key = persona if persona in examples else "student (beginner)"
    # Feedback and output formatting
    feedback_note = "If this answer was helpful, let us know! If not, please suggest how it could be improved."
    return f"""
You are a helpful assistant for a {persona_key}.
{persona_instructions[persona_key]}
Always answer ONLY about the repository itself. Do not mention cloning or scraping. If you are unsure, say you don't know.
Format your answer in markdown with bullet points, code blocks, and headings where appropriate.

Here are some example Q&A for your style:
{examples[persona_key]}

Repository metadata:
{meta_pretty}

Important files (truncated):
{files_block}

Digest excerpt (truncated):
{digest_snippet}

{feedback_note}
""".strip()


# ---------- Routes ----------
@app.route("/")
def index():
    return render_template("index.html")


@app.route("/load_repo", methods=["POST"])
def load_repo():
    """Load repo: metadata + clone + digest."""
    data = request.get_json(force=True)
    repo_url = (data.get("repo_url") or "").strip()
    if not repo_url or "github.com" not in repo_url:
        return jsonify({"error": "Please provide a valid GitHub repository URL."}), 400

    try:
        metadata = fetch_github_metadata(repo_url)
        digest_full, files = clone_and_collect(repo_url)

        digest_trimmed = digest_full[:MAX_CONTEXT_CHARS]

        repo_cache.clear()
        repo_cache.update({
            "repo_url": repo_url,
            "metadata": metadata,
            "digest": digest_trimmed,
            "files": files,
        })

        return jsonify({
            "message": "Repository loaded successfully.",
            "metadata": metadata,
            "files": files[:200],
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/ask", methods=["POST"])
def ask():
    """Answer user questions about the repo."""
    data = request.get_json(force=True)
    question = (data.get("question") or "").strip()
    persona = (data.get("persona") or "student (beginner)").strip()

    if not question:
        return jsonify({"error": "Missing question."}), 400
    if "digest" not in repo_cache:
        return jsonify({"error": "No repository loaded yet. Please load a repository first."}), 400
    if not GEMINI_API_KEY:
        return jsonify({"error": "Gemini API key not configured. Set GEMINI_API_KEY env var."}), 500

    # Persona memory: store last used persona in cache
    repo_cache["last_persona"] = persona

    try:
        digest = repo_cache["digest"]
        files = repo_cache.get("files", [])
        metadata = repo_cache["metadata"]

        # Context limiting: only most relevant files (top 20)
        system_prompt = build_system_prompt(metadata, files, digest[:8000], persona)
        # Clarifying question logic
        if len(question.split()) < 3:
            user_prompt = f"The user question is very short. If you need clarification, ask a clarifying question first.\nQuestion: {question}\nAnswer for a {persona}."
        else:
            user_prompt = f"Question: {question}\nAnswer for a {persona}."

        model = genai.GenerativeModel(MODEL_NAME)
        response = model.generate_content(system_prompt + "\n\n" + user_prompt)
        text = getattr(response, "text", str(response))
        # Error handling: check for empty or generic answers
        if not text or text.strip().lower() in {"i don't know", "not sure", "unknown"}:
            return jsonify({"answer": "Sorry, I couldn't find an answer. Please try rephrasing your question or ask for a specific file/module."})
        return jsonify({"answer": text.strip()})
    except Exception as e:
        return jsonify({"error": f"An error occurred: {str(e)}"}), 500


@app.route("/process_repo", methods=["POST"])
def process_repo():
    """Generate a role-based summary of repo."""
    role = request.form.get("role")
    repo_url = request.form.get("repo_link")

    if not repo_url or not role:
        return jsonify({"error": "Missing repo link or role."}), 400

    try:
        merged_text = process_repository(repo_url)
        model = genai.GenerativeModel(MODEL_NAME)

        if role == "developer":
            prompt = f"You are a senior developer reviewing this repository.\nSummarize its modules, dependencies, flow, and improvements.\n\n{merged_text[:2000]}"
        elif role == "beginner":
            prompt = f"You are a programming teacher explaining this repository.\nSummarize it simply with analogies.\n\n{merged_text[:2000]}"
        elif role == "project_manager":
            prompt = f"You are a project manager.\nSummarize the purpose, features, tech stack, risks, and complexity.\n\n{merged_text[:2000]}"
        else:
            prompt = f"Summarize this repository:\n\n{merged_text[:2000]}"

        response = model.generate_content(prompt)
        summary = response.text.strip() if hasattr(response, "text") else str(response)

        return jsonify({"summary": summary})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/health")
def health():
    return jsonify({"status": "ok", "has_repo": "digest" in repo_cache})


if __name__ == "__main__":
    app.run(debug=True)
