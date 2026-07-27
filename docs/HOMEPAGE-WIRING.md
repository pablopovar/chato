# Wiring `/#chat` to Chato

The live homepage already presents the Chato interface and identifies Nerdo as the knowledge-and-operations participant. The API client uses data attributes so the existing visual markup can remain unchanged.

## Required hooks

```html
<section id="chat" data-chato-interface>
  <div data-chato-messages aria-live="polite"></div>

  <button type="button" data-chato-suggestion="Ask Chato how it works">
    Ask Chato how it works
  </button>

  <button type="button" data-chato-suggestion="Chato my website">
    Chato my website
  </button>

  <form data-chato-site-form hidden>
    <input data-chato-website type="url" required placeholder="https://yourwebsite.com">
    <input data-chato-email type="email" required placeholder="you@example.com">
    <button type="submit">Start</button>
  </form>

  <form data-chato-form>
    <input data-chato-input type="text" required placeholder="Message Chato">
    <button type="submit" aria-label="Send">↑</button>
  </form>

  <p data-chato-status aria-live="polite"></p>
</section>

<script type="module">
  import { mountChatoInterfaces } from "/js/chato-nerdo-chat-controller.js";
  mountChatoInterfaces({ baseUrl: "/api" });
</script>
```

## Security boundary

The public page receives a per-site `site_token` after intake and can use Chato. It must never receive `X-Nerdo-Key`. Owner-authorized Nerdo conversations should be entered through a verified link, authenticated email flow, or later customer identity layer.
