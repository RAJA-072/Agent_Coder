// =========================
// Chatbot Flow
// =========================

async function loadRepo() {
  const repoUrl = document.getElementById("repo-url").value.trim();
  if (!repoUrl) {
    alert("Please enter a GitHub repo URL");
    return;
  }

  const filesDiv = document.getElementById("files");
  filesDiv.textContent = "Loading repository…";
  try {
    const resp = await fetch("/load_repo", {
      method: "POST",
      headers: { "Content-Type": "application/x-www-form-urlencoded" },
      body: "repo_url=" + encodeURIComponent(repoUrl),
    });
    let data;
    try {
      data = await resp.json();
    } catch (jsonErr) {
      filesDiv.textContent = "";
      return;
    }
    if (!resp.ok) {
      filesDiv.textContent = "Error: " + (data.error || "Failed to load repository");
      return;
    }
    filesDiv.textContent = data.message;
  } catch (e) {
    filesDiv.textContent = "Error: " + e.message;
  }
}

async function askQuestion() {
  const q = document.getElementById("question").value.trim();
  if (!q) {
    alert("Please enter a question");
    return;
  }

  const chatBox = document.getElementById("chat-box");
  const p = document.createElement("p");
  p.textContent = "You: " + q;
  chatBox.appendChild(p);

  try {
    const resp = await fetch("/ask", {
      method: "POST",
      headers: { "Content-Type": "application/x-www-form-urlencoded" },
      body: "question=" + encodeURIComponent(q),
    });

    if (!resp.ok) throw new Error("Failed to get answer");

    const data = await resp.json();
    const ans = document.createElement("p");
    ans.textContent = "Bot: " + data.answer;
    chatBox.appendChild(ans);
  } catch (e) {
    const err = document.createElement("p");
    err.textContent = "Error: " + e.message;
    chatBox.appendChild(err);
  }
}

// =========================
// Role-based Summary Flow
// =========================

async function generateSummary() {
  const role = document.getElementById("summary-role").value.trim();
  const repoUrl = document.getElementById("summary-repo").value.trim();

  if (!repoUrl || !role) {
    document.getElementById("summary-box").textContent =
      "Please enter both repo URL and role.";
    return;
  }

  const summaryBox = document.getElementById("summary-box");
  summaryBox.textContent = "Generating summary…";
  let data = null;
  try {
    const formData = new FormData();
    formData.append("role", role);
    formData.append("repo_link", repoUrl);

    const resp = await fetch("/process_repo", {
      method: "POST",
      body: formData,
    });

    try {
      data = await resp.json();
    } catch (jsonErr) {
      // Only show error if fetch itself fails, not before
      summaryBox.textContent = "Error: Unexpected response. Please check the backend.";
      return;
    }
    if (!resp.ok) {
      summaryBox.textContent = "Error: " + (data.error || "Failed");
      return;
    }
    summaryBox.textContent = data.summary;
  } catch (e) {
    summaryBox.textContent = "Error: " + e.message;
  }
}

// =========================
// Event Listeners
// =========================
document.getElementById("load-btn")?.addEventListener("click", loadRepo);
document.getElementById("ask-btn")?.addEventListener("click", askQuestion);
document
  .getElementById("summary-btn")
  ?.addEventListener("click", generateSummary);
