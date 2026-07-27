export class ChatoNerdoApiError extends Error {
  constructor(message, { status = 0, code = null, payload = null } = {}) {
    super(message);
    this.name = "ChatoNerdoApiError";
    this.status = status;
    this.code = code;
    this.payload = payload;
  }
}

export class ChatoNerdoApi {
  constructor({
    baseUrl = "/api",
    siteToken = null,
    nerdoKey = null,
    timeoutMs = 30000
  } = {}) {
    this.baseUrl = baseUrl.replace(/\/$/, "");
    this.siteToken = siteToken;
    this.nerdoKey = nerdoKey;
    this.timeoutMs = timeoutMs;
  }

  setSiteToken(token) { this.siteToken = token || null; }
  setNerdoKey(token) { this.nerdoKey = token || null; }

  async request(path, { method = "GET", body, headers = {}, signal } = {}) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), this.timeoutMs);
    const abort = signal
      ? AbortSignal.any([controller.signal, signal])
      : controller.signal;

    const requestHeaders = { Accept: "application/json", ...headers };
    if (this.siteToken) requestHeaders["X-Site-Token"] = this.siteToken;
    if (this.nerdoKey) requestHeaders["X-Nerdo-Key"] = this.nerdoKey;
    if (body !== undefined && !(body instanceof FormData)) {
      requestHeaders["Content-Type"] = "application/json";
      body = JSON.stringify(body);
    }

    try {
      const response = await fetch(`${this.baseUrl}${path}`, {
        method,
        headers: requestHeaders,
        body,
        signal: abort,
        credentials: "same-origin"
      });
      const contentType = response.headers.get("content-type") || "";
      const payload = contentType.includes("application/json")
        ? await response.json()
        : await response.text();
      if (!response.ok) {
        const message = payload?.detail || payload?.message || `${response.status} ${response.statusText}`;
        throw new ChatoNerdoApiError(message, { status: response.status, payload });
      }
      return payload;
    } catch (error) {
      if (error?.name === "AbortError") {
        throw new ChatoNerdoApiError("The request timed out.", { code: "timeout" });
      }
      throw error;
    } finally {
      clearTimeout(timer);
    }
  }

  capabilities() { return this.request("/v1/capabilities"); }

  createSite({ websiteUrl, email, businessName = null }) {
    return this.request("/v1/sites", {
      method: "POST",
      body: { website_url: websiteUrl, email, business_name: businessName }
    });
  }

  getSite(siteId) { return this.request(`/v1/sites/${encodeURIComponent(siteId)}`); }

  attachActivation(siteId, { domain, botKey }) {
    return this.request(`/v1/sites/${encodeURIComponent(siteId)}/activation`, {
      method: "POST",
      body: { domain, bot_key: botKey }
    });
  }

  createConversation({ persona = "chato", siteId = null } = {}) {
    return this.request("/v1/conversations", {
      method: "POST",
      body: { persona, site_id: siteId }
    });
  }

  sendMessage(conversationId, content, context = {}) {
    return this.request(`/v1/conversations/${encodeURIComponent(conversationId)}/messages`, {
      method: "POST",
      body: { content, context }
    });
  }

  listMessages(conversationId) {
    return this.request(`/v1/conversations/${encodeURIComponent(conversationId)}/messages`);
  }

  listSources(siteId) {
    return this.request(`/v1/sites/${encodeURIComponent(siteId)}/sources`);
  }

  refreshSources(siteId) {
    return this.request(`/v1/sites/${encodeURIComponent(siteId)}/sources/refresh`, { method: "POST" });
  }

  sourceChanges(siteId, { captureCurrent = true } = {}) {
    return this.request(`/v1/sites/${encodeURIComponent(siteId)}/sources/changes`, {
      method: "POST",
      body: { capture_current: captureCurrent }
    });
  }

  findContradictions(siteId, { minimumConfidence = 0.45, limit = 50 } = {}) {
    return this.request(`/v1/sites/${encodeURIComponent(siteId)}/knowledge/contradictions`, {
      method: "POST",
      body: { minimum_confidence: minimumConfidence, limit }
    });
  }

  diagnoseAnswer(siteId, { question, answer, searchLimit = 10 }) {
    return this.request(`/v1/sites/${encodeURIComponent(siteId)}/answers/diagnose`, {
      method: "POST",
      body: { question, answer, search_limit: searchLimit }
    });
  }

  correctAnswer(siteId, { answerId = null, question, originalAnswer, correction }) {
    return this.request(`/v1/sites/${encodeURIComponent(siteId)}/answers/corrections`, {
      method: "POST",
      body: {
        answer_id: answerId,
        question,
        original_answer: originalAnswer,
        correction
      }
    });
  }

  listIntegrations(siteId) {
    return this.request(`/v1/sites/${encodeURIComponent(siteId)}/integrations`);
  }

  connectIntegration(siteId, { kind, targetUrl = null, label = null, configuration = {} }) {
    return this.request(`/v1/sites/${encodeURIComponent(siteId)}/integrations`, {
      method: "POST",
      body: { kind, target_url: targetUrl, label, configuration }
    });
  }

  connectWordPress(siteId, { targetUrl, widgetScriptUrl, label = "WordPress" }) {
    return this.connectIntegration(siteId, {
      kind: "wordpress",
      targetUrl,
      label,
      configuration: { widget_script_url: widgetScriptUrl }
    });
  }

  updateIntegration(siteId, integrationId, changes) {
    return this.request(
      `/v1/sites/${encodeURIComponent(siteId)}/integrations/${encodeURIComponent(integrationId)}`,
      { method: "PATCH", body: changes }
    );
  }

  disconnectIntegration(siteId, integrationId) {
    return this.request(
      `/v1/sites/${encodeURIComponent(siteId)}/integrations/${encodeURIComponent(integrationId)}`,
      { method: "DELETE" }
    );
  }

  verifyInstallation(siteId, integrationId) {
    return this.request(
      `/v1/sites/${encodeURIComponent(siteId)}/integrations/${encodeURIComponent(integrationId)}/verify`,
      { method: "POST" }
    );
  }

  getOperation(operationId) {
    return this.request(`/v1/operations/${encodeURIComponent(operationId)}`);
  }

  async waitForOperation(operationId, { intervalMs = 1500, timeoutMs = 120000 } = {}) {
    const started = Date.now();
    while (true) {
      const operation = await this.getOperation(operationId);
      if (["completed", "failed", "blocked", "needs_input"].includes(operation.status)) {
        return operation;
      }
      if (Date.now() - started > timeoutMs) {
        throw new ChatoNerdoApiError("The operation did not finish before the client timeout.", { code: "operation_timeout" });
      }
      await new Promise(resolve => setTimeout(resolve, intervalMs));
    }
  }
}
