import { ChatoNerdoApi } from "./nerdo-api.js";

const selectors = {
  form: "[data-chato-form]",
  input: "[data-chato-input]",
  messages: "[data-chato-messages]",
  suggestions: "[data-chato-suggestion]",
  siteForm: "[data-chato-site-form]",
  website: "[data-chato-website]",
  email: "[data-chato-email]",
  status: "[data-chato-status]"
};

function textElement(tag, text, className) {
  const element = document.createElement(tag);
  element.textContent = text;
  if (className) element.className = className;
  return element;
}

export class ChatoInterface {
  constructor(root, options = {}) {
    this.root = root;
    this.api = options.api || new ChatoNerdoApi({ baseUrl: options.baseUrl || "/api" });
    this.conversationId = null;
    this.siteId = null;
    this.form = root.querySelector(selectors.form);
    this.input = root.querySelector(selectors.input);
    this.messages = root.querySelector(selectors.messages);
    this.siteForm = root.querySelector(selectors.siteForm);
    this.status = root.querySelector(selectors.status);
  }

  async start() {
    if (!this.form || !this.input || !this.messages) {
      throw new Error("The Chato interface is missing required data attributes.");
    }
    const conversation = await this.api.createConversation({ persona: "chato" });
    this.conversationId = conversation.conversation_id;
    this.bind();
  }

  bind() {
    this.form.addEventListener("submit", async event => {
      event.preventDefault();
      const content = this.input.value.trim();
      if (!content) return;
      this.input.value = "";
      await this.send(content);
    });

    this.root.querySelectorAll(selectors.suggestions).forEach(button => {
      button.addEventListener("click", async () => {
        const content = button.dataset.chatoSuggestion || button.textContent.trim();
        if (content.toLowerCase().includes("my website") && this.siteForm) {
          this.siteForm.hidden = false;
          this.siteForm.querySelector(selectors.website)?.focus();
          return;
        }
        await this.send(content);
      });
    });

    if (this.siteForm) {
      this.siteForm.addEventListener("submit", async event => {
        event.preventDefault();
        const websiteUrl = this.siteForm.querySelector(selectors.website)?.value.trim();
        const email = this.siteForm.querySelector(selectors.email)?.value.trim();
        if (!websiteUrl || !email) return;
        this.setStatus("Nerdo is starting the website intake…");
        try {
          const site = await this.api.createSite({ websiteUrl, email });
          this.siteId = site.site_id;
          this.api.setSiteToken(site.site_token);
          const conversation = await this.api.createConversation({ persona: "chato", siteId: this.siteId });
          this.conversationId = conversation.conversation_id;
          this.append("assistant", "Nerdo started preparing the website. Chato will continue here when the site is ready.");
          this.siteForm.hidden = true;
          this.setStatus(`Website status: ${site.status}`);
        } catch (error) {
          this.setStatus(error.message, true);
        }
      });
    }
  }

  async send(content) {
    this.append("user", content);
    this.setBusy(true);
    try {
      const reply = await this.api.sendMessage(this.conversationId, content);
      this.append("assistant", reply.message.content);
      if (reply.needs_input?.length) {
        this.setStatus(`Needed: ${reply.needs_input.join(", ")}`);
      } else {
        this.setStatus("");
      }
    } catch (error) {
      this.append("assistant", `I could not complete that request: ${error.message}`);
      this.setStatus(error.message, true);
    } finally {
      this.setBusy(false);
    }
  }

  append(role, content) {
    const article = document.createElement("article");
    article.className = `chat-message chat-message--${role}`;
    article.append(textElement("strong", role === "user" ? "You" : "Chato"));
    article.append(textElement("p", content));
    this.messages.append(article);
    article.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }

  setBusy(busy) {
    this.form.querySelector("button[type='submit']")?.toggleAttribute("disabled", busy);
    this.input.toggleAttribute("disabled", busy);
  }

  setStatus(message, isError = false) {
    if (!this.status) return;
    this.status.textContent = message;
    this.status.dataset.state = isError ? "error" : "normal";
  }
}

export async function mountChatoInterfaces(options = {}) {
  const roots = document.querySelectorAll("[data-chato-interface]");
  const instances = [];
  for (const root of roots) {
    const instance = new ChatoInterface(root, options);
    await instance.start();
    instances.push(instance);
  }
  return instances;
}
