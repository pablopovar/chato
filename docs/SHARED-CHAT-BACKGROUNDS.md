# Shared Chato page backgrounds

Each active domain may have one optional presentation background for shared Chato sessions.

The background is intended to be a full-page screenshot of the website. It gives the recipient visual context for where Chato would appear if the conversation were embedded on the real site.

## Operator workflow

1. Open `/dashboard/<domain>`.
2. Open **Session Share**.
3. Upload a PNG, JPEG, or WebP screenshot under **Shared-page background**.
4. Review the preview, which overlays a representative Chato panel on the screenshot.
5. Create the normal one-time shared-session link.

The background applies at the domain level. Existing and new share sessions use the currently configured image.

## Storage

The image is stored outside the Markdown corpus:

```text
users/<owner>/<domain>/share-assets/background.<ext>
users/<owner>/<domain>/share-assets/background.json
```

It is not crawled, indexed, retrieved, or provided to the language model.

Supported types:

- PNG
- JPEG
- WebP

The default maximum upload size is 20,000,000 bytes. Override it with:

```dotenv
NERDO_SHARE_BACKGROUND_MAX_BYTES=20000000
```

## Public behavior

A claimed session requests its background through:

```text
/nerdo/share/session/<session-id>/background
```

The same HttpOnly session credential that protects the conversation protects the image. The image is not exposed as an unrestricted domain asset.

When a background is present, the page fills the browser viewport with the website screenshot and positions the live Chato panel along the right side. On narrow screens, Chato becomes a full-screen panel and the screenshot is dimmed behind it.

When no background is configured, the shared session uses the standard Chato page.
