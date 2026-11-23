(() => {
  const API_BASE = (window.SOUL_API_BASE_URL || "").replace(/\/$/, "");
  const API = API_BASE ? `${API_BASE}/api` : "";
  const IDS_KEY = "soulDocumentIds";
  const NAMES_KEY = "soulDocumentNames";
  let selectedFiles = [];

  // The built UI clears the native input after updating React state. Capture
  // File objects before React handles change so the backend receives originals.
  document.addEventListener("change", (event) => {
    if (event.target.matches('.intake-drop input[type="file"]')) {
      selectedFiles = [...selectedFiles, ...Array.from(event.target.files || [])];
    }
  }, true);
  document.addEventListener("drop", (event) => {
    if (event.target.closest(".intake-drop")) {
      selectedFiles = [...selectedFiles, ...Array.from(event.dataTransfer?.files || [])];
    }
  }, true);

  const setNotice = (message, error = false) => {
    const notice = document.querySelector(".workflow-notice");
    if (!notice) return;
    notice.textContent = message;
    notice.setAttribute("role", error ? "alert" : "status");
    notice.dataset.error = String(error);
  };

  const fileBase64 = (file) => new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onerror = () => reject(new Error(`Could not read ${file.name}.`));
    reader.onload = () => resolve(String(reader.result).split(",", 2)[1]);
    reader.readAsDataURL(file);
  });

  async function apiPost(path, body) {
    if (!API) throw new Error("The Render backend URL is not configured. Set SOUL_API_BASE_URL in dist/api-config.js and redeploy the frontend.");
    let response;
    try {
      response = await fetch(`${API}${path}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
    } catch (_) {
      throw new Error("Could not reach the SOUL AI backend. Check that it is running.");
    }
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(payload.detail || `Backend request failed (${response.status}).`);
    if (payload.status === "error") throw new Error(payload.error?.message || "PDF processing failed.");
    return payload;
  }

  async function uploadSelectedFiles() {
    const files = selectedFiles.slice();
    if (!files.length) return;
    const invalid = files.find((file) => !file.name.toLowerCase().endsWith(".pdf") || file.size > 25 * 1024 * 1024);
    if (invalid) {
      setNotice(`${invalid.name} is invalid. Select PDF files up to 25 MB each.`, true);
      return;
    }

    const button = Array.from(document.querySelectorAll("button.primary-button"))
      .find((item) => item.textContent.includes("Start demo verification"));
    if (button) button.disabled = true;
    setNotice("Uploading PDFs and extracting page evidence…");
    try {
      const documents = await Promise.all(files.map(async (file) => ({
        document_id: crypto.randomUUID(),
        filename: file.name,
        content_base64: await fileBase64(file),
      })));
      const response = await apiPost("/soul-ai/documents/extract", {
        request_id: crypto.randomUUID(), documents,
      });
      const extracted = response.data.documents;
      sessionStorage.setItem(IDS_KEY, JSON.stringify(extracted.map((doc) => doc.document_id)));
      sessionStorage.setItem(NAMES_KEY, JSON.stringify(extracted.map((doc) => doc.filename)));
      setNotice(`${extracted.length} PDF(s) processed; ${response.data.evidence_count} evidence excerpts indexed. Opening verification…`);
      window.location.assign("/verification");
    } catch (error) {
      if (button) button.disabled = false;
      setNotice(error.message || "PDF upload failed.", true);
    }
  }

  document.addEventListener("click", (event) => {
    const removeButton = event.target.closest('button.icon-button[aria-label^="Remove "]');
    if (removeButton) {
      const filename = removeButton.getAttribute("aria-label").slice("Remove ".length);
      const removeIndex = selectedFiles.findIndex((file) => file.name === filename);
      if (removeIndex >= 0) selectedFiles.splice(removeIndex, 1);
      return;
    }
    const button = event.target.closest("button.primary-button");
    if (!button || !button.textContent.includes("Start demo verification")) return;
    event.preventDefault();
    event.stopImmediatePropagation();
    uploadSelectedFiles();
  }, true);

  function addText(parent, tag, className, value) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    node.textContent = value;
    parent.append(node);
    return node;
  }

  function renderEvidenceCard(container, status, claimText, citations = []) {
    const card = document.createElement("article");
    card.className = "panel";
    addText(card, "b", `claim-status ${status}`, status === "supported" ? "SUPPORTED" : status === "contradicted" ? "CONTRADICTED" : "INSUFFICIENT EVIDENCE");
    addText(card, "p", "", claimText);
    citations.forEach((citation) => {
      const source = document.createElement("a");
      source.className = "secondary-button";
      source.textContent = `${citation.filename}, page ${citation.page_number}`;
      const documentId = citation.document_id || documentIdByName[citation.filename];
      if (documentId) {
        source.href = `${API}/soul-ai/documents/${encodeURIComponent(documentId)}/file#page=${citation.page_number}`;
        source.target = "_blank";
        source.rel = "noopener";
      }
      card.append(source);
      addText(card, "blockquote", "", citation.text || citation.snippet || "");
    });
    container.append(card);
  }

  let documentIdByName = {};
  function mountQuestionPanel() {
    if (!location.pathname.startsWith("/verification")) return;
    const page = document.querySelector(".page-stack");
    if (!page || page.querySelector("#soul-live-question")) return;

    let ids = [];
    let names = [];
    try {
      ids = JSON.parse(sessionStorage.getItem(IDS_KEY) || "[]");
      names = JSON.parse(sessionStorage.getItem(NAMES_KEY) || "[]");
    } catch (_) { /* The form remains available and reports missing uploads. */ }
    documentIdByName = {};
    names.forEach((name, index) => {
      if (names.filter((candidate) => candidate === name).length === 1) documentIdByName[name] = ids[index];
    });

    const panel = document.createElement("section");
    panel.id = "soul-live-question";
    panel.className = "panel page-stack";
    panel.style.marginBottom = "24px";
    addText(panel, "h2", "", "Ask about your uploaded PDFs");
    const form = document.createElement("form");
    form.style.cssText = "display:flex;gap:12px;flex-wrap:wrap;margin:16px 0";
    const input = document.createElement("input");
    input.type = "text";
    input.required = true;
    input.maxLength = 2000;
    input.placeholder = "Ask a question grounded in the uploaded documents";
    input.setAttribute("aria-label", "Question about uploaded PDFs");
    input.style.cssText = "flex:1;min-width:240px;padding:12px;border:1px solid #b9bec9;border-radius:8px";
    const submit = document.createElement("button");
    submit.type = "submit";
    submit.className = "primary-button";
    submit.textContent = "Verify question";
    form.append(input, submit);
    panel.append(form);
    const message = addText(panel, "p", "", ids.length ? "Answers are checked against the uploaded source evidence." : "Upload PDFs from the Upload page before asking a question.");
    message.setAttribute("aria-live", "polite");
    const results = document.createElement("div");
    results.className = "page-stack";
    panel.append(results);
    page.prepend(panel);

    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      const question = input.value.trim();
      if (!question) return;
      if (!ids.length) {
        message.textContent = "No uploaded PDFs are available in this session. Upload documents first.";
        return;
      }
      submit.disabled = true;
      results.replaceChildren();
      message.textContent = "Retrieving evidence and independently verifying claims…";
      try {
        const response = await apiPost("/soul-ai/answer", {
          request_id: crypto.randomUUID(), question, document_ids: ids,
        });
        const answer = response.data;
        message.textContent = answer.answer;
        (answer.claims || []).forEach((claim) => renderEvidenceCard(results, "supported", claim.claim_text, claim.evidence || []));
        (answer.conflicts || []).forEach((conflict) => renderEvidenceCard(
          results,
          "contradicted",
          `${conflict.entity} — ${conflict.attribute}: ${conflict.value_1} versus ${conflict.value_2}`,
          [conflict.source_1, conflict.source_2],
        ));
        if (!answer.claims?.length && !answer.conflicts?.length) {
          renderEvidenceCard(results, "unsupported", answer.answer || "Insufficient evidence.");
        }
      } catch (error) {
        message.textContent = error.message || "Question verification failed.";
      } finally {
        submit.disabled = false;
      }
    });
  }

  function syncUploadPage() {
    if (location.pathname !== "/upload") return;
    const input = document.querySelector('.intake-drop input[type="file"]');
    if (input) input.accept = ".pdf,application/pdf";
    const intro = document.querySelector(".workflow-heading p:last-child");
    if (intro && intro.textContent !== "Choose up to 10 PDF files for document verification.") {
      intro.textContent = "Choose up to 10 PDF files for document verification.";
    }
    const hint = document.querySelector(".intake-drop p");
    if (hint && hint.textContent !== "PDF only · 25 MB per file") hint.textContent = "PDF only · 25 MB per file";
  }

  function syncRoute() {
    syncUploadPage();
    mountQuestionPanel();
  }

  const observer = new MutationObserver(syncRoute);
  observer.observe(document.documentElement, { childList: true, subtree: true });
  syncRoute();
  window.addEventListener("popstate", syncRoute);
})();
