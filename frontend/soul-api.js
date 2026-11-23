(function () {
  const apiBaseUrl = (window.SOUL_API_BASE_URL || "").replace(/\/$/, "");
  const requestId = () => (window.crypto && crypto.randomUUID ? crypto.randomUUID() : `req-${Date.now()}-${Math.random().toString(16).slice(2)}`);

  async function request(path, body) {
    if (!apiBaseUrl) throw new Error("SOUL_API_BASE_URL is not configured for this frontend.");
    let response;
    try {
      response = await fetch(`${apiBaseUrl}/api${path}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
    } catch (_) {
      throw new Error(`Could not reach the SOUL AI backend at ${apiBaseUrl}.`);
    }
    let payload;
    try { payload = await response.json(); } catch (_) { payload = {}; }
    if (!response.ok) throw new Error(payload.detail || `The API returned HTTP ${response.status}.`);
    if (payload.status === "error") throw new Error(payload.error?.message || "The AI engine could not complete the request.");
    return payload;
  }

  function fileAsBase64(file) {
    return new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.onerror = () => reject(new Error(`Could not read ${file.name}.`));
      reader.onload = () => resolve(String(reader.result).split(",", 2)[1]);
      reader.readAsDataURL(file);
    });
  }

  async function upload(files) {
    const documents = await Promise.all(files.map(async (file) => ({
      document_id: requestId(), filename: file.name, content_base64: await fileAsBase64(file),
    })));
    const result = await request("/soul-ai/documents/extract", { request_id: requestId(), documents });
    const ids = result.data.documents.map((doc) => doc.document_id);
    localStorage.setItem("soulDocumentIds", JSON.stringify(ids));
    localStorage.setItem("soulDocumentNames", JSON.stringify(result.data.documents.map((doc) => doc.filename)));
    return result.data;
  }

  function getDocumentIds() {
    try { return JSON.parse(localStorage.getItem("soulDocumentIds") || "[]"); } catch (_) { return []; }
  }

  async function answer(question) {
    return request("/soul-ai/answer", {
      request_id: requestId(), question, document_ids: getDocumentIds(),
    });
  }

  window.SoulAPI = { upload, answer, getDocumentIds };
})();
